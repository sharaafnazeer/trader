"""A coin can present more than one setup at once, and the engine must say so.

The trend-pullback checklist (1A/1B) and the breakout family (2A/2B) are judged
independently — a breakout does not gate on the moving-average stack, so neither reading can
stand in for the other. These cover which verdict decides the row, and the case that
matters most: two setups that disagree about direction.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from trader.direction import Direction
from trader.market_data import OHLCV_COLUMNS, Candles, OrderBook
from trader.runner import CoinAnalysis, primary_verdict
from trader.strategy import Condition, EntryStatus, SetupVerdict, StrategyId


def _verdict(
    strategy: StrategyId | None,
    *,
    direction: Direction,
    ready: bool,
    met: int = 7,
    symbol: str = "AAA",
) -> SetupVerdict:
    rows = tuple(
        Condition(f"c{i}", i < met, f"condition {i}") for i in range(7)
    )
    return SetupVerdict(
        symbol=symbol,
        trend=direction,
        strategy=strategy,
        entry_status=EntryStatus.READY if ready else EntryStatus.NOT_READY,
        decision=direction if ready else Direction.NONE,
        reason="every evaluated condition is met" if ready else "something is unmet",
        conditions=rows,
        conditions_evaluated=7,
    )


def _analysis(*verdicts: SetupVerdict, direction: Direction = Direction.LONG) -> CoinAnalysis:
    return CoinAnalysis(
        symbol="AAA",
        direction=direction,
        features_by_tf={},
        structure_by_tf={},
        candles_by_tf={
            "4h": Candles("AAA", "4h", pd.DataFrame(columns=OHLCV_COLUMNS))
        },
        order_book=OrderBook(
            symbol="AAA", best_bid=1.0, best_ask=1.0, bid_depth=1.0, ask_depth=1.0
        ),
        verdicts=verdicts,
    )


# --- Which verdict decides the row -------------------------------------------------


def test_no_verdicts_decides_nothing() -> None:
    assert primary_verdict(()) is None


def test_a_lone_ready_setup_decides() -> None:
    breakout = _verdict(StrategyId.BREAKOUT_LONG, direction=Direction.LONG, ready=True)
    pullback = _verdict(
        StrategyId.TREND_PULLBACK_LONG, direction=Direction.LONG, ready=False, met=5
    )

    assert primary_verdict((pullback, breakout)) is breakout


def test_with_nothing_ready_the_checklist_nearest_to_complete_decides() -> None:
    """The most informative WAIT names the condition the coin is actually short of."""

    near = _verdict(StrategyId.BREAKOUT_LONG, direction=Direction.LONG, ready=False, met=6)
    far = _verdict(
        StrategyId.TREND_PULLBACK_LONG, direction=Direction.LONG, ready=False, met=2
    )

    assert primary_verdict((far, near)) is near


def test_a_coin_matching_no_setup_keeps_its_no_setup_reason() -> None:
    none_matched = _verdict(None, direction=Direction.NONE, ready=False, met=0)

    decided = primary_verdict((none_matched,))

    assert decided is none_matched
    assert decided.strategy is None


def test_two_ready_setups_agreeing_on_direction_are_taken() -> None:
    pullback = _verdict(
        StrategyId.TREND_PULLBACK_LONG, direction=Direction.LONG, ready=True
    )
    breakout = _verdict(StrategyId.BREAKOUT_LONG, direction=Direction.LONG, ready=True)

    decided = primary_verdict((pullback, breakout))

    assert decided is not None
    assert decided.entry_status is EntryStatus.READY
    assert decided.decision is Direction.LONG


# --- The case that matters: two setups disagreeing ---------------------------------


def test_two_ready_setups_disagreeing_on_direction_take_neither() -> None:
    """A coin read both ways is not a strong signal — it is an unclear one."""

    pullback = _verdict(
        StrategyId.TREND_PULLBACK_SHORT, direction=Direction.SHORT, ready=True
    )
    breakout = _verdict(StrategyId.BREAKOUT_LONG, direction=Direction.LONG, ready=True)

    decided = primary_verdict((pullback, breakout))

    assert decided is not None
    assert decided.entry_status is EntryStatus.NOT_READY
    assert decided.decision is Direction.NONE
    assert "disagree on direction" in decided.reason
    assert "1B" in decided.reason and "2A" in decided.reason


def test_a_disagreement_is_not_resolved_by_rank() -> None:
    """Even a stronger checklist does not win a direction conflict outright."""

    strong = replace(
        _verdict(StrategyId.BREAKOUT_LONG, direction=Direction.LONG, ready=True),
        conditions_evaluated=7,
    )
    weak = _verdict(
        StrategyId.TREND_PULLBACK_SHORT, direction=Direction.SHORT, ready=True, met=7
    )

    decided = primary_verdict((strong, weak))

    assert decided is not None
    assert decided.decision is Direction.NONE


# --- What the analysis row reports -------------------------------------------------


def test_the_analysis_exposes_every_matched_setup() -> None:
    analysis = _analysis(
        _verdict(StrategyId.TREND_PULLBACK_LONG, direction=Direction.LONG, ready=False, met=4),
        _verdict(StrategyId.BREAKOUT_LONG, direction=Direction.LONG, ready=True),
    )

    assert len(analysis.verdicts) == 2
    assert analysis.verdict is not None
    assert analysis.verdict.strategy is StrategyId.BREAKOUT_LONG


def test_a_breakout_resolves_a_direction_the_trend_rule_did_not() -> None:
    """The family's defining case: a level breaks with no established trend behind it."""

    analysis = _analysis(
        _verdict(StrategyId.BREAKOUT_LONG, direction=Direction.LONG, ready=True),
        direction=Direction.NONE,
    )

    assert analysis.direction is Direction.NONE
    assert analysis.setup_direction is Direction.LONG


def test_setup_direction_falls_back_to_the_trend_rule_when_nothing_matched() -> None:
    analysis = _analysis(direction=Direction.SHORT)

    assert analysis.verdicts == ()
    assert analysis.verdict is None
    assert analysis.setup_direction is Direction.SHORT


# --- End to end through the runner -------------------------------------------------


def _zigzag_frame() -> pd.DataFrame:
    """Rallies that tag 100 and fail, pullbacks that leave swing lows, then a break."""

    rows: list[list[float]] = []
    t = 0.0
    price = 92.0
    for cycle in range(14):
        for target in (100.0, 92.5 + cycle * 0.05):
            step = 1.2 if target > price else -1.2
            while abs(price - target) > 1.2:
                price += step
                rows.append([t, price - step * 0.3, price + 0.4, price - 0.4, price, 1_200_000.0])
                t += 14_400_000
            price = target
            rows.append([t, price - step * 0.3, price + 0.5, price - 0.5, price, 1_200_000.0])
            t += 14_400_000
    rows.append([t, 99.4, 102.8, 99.2, 102.2, 3_000_000.0])
    return pd.DataFrame(rows, columns=OHLCV_COLUMNS)


class _FakeMarket:
    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int) -> Candles:
        return Candles(symbol, timeframe, self._frame)

    def get_order_book(self, symbol: str, limit: int = 20) -> OrderBook:
        return OrderBook(
            symbol=symbol,
            best_bid=102.1,
            best_ask=102.3,
            bid_depth=500_000.0,
            ask_depth=500_000.0,
        )


class _NoTradingView:
    def get_analysis_batch(self, *args: object, **kwargs: object) -> dict[str, object]:
        return {}


def test_a_breakout_surfaces_on_a_coin_the_trend_rule_called_directionless() -> None:
    """The family's whole reason to exist, end to end through the runner.

    The trend rule resolves nothing, so the pullback checklist matches no setup and would
    never have planned this coin. The breakout resolves LONG from the level it broke, the
    plan is built in *that* direction, and the coin reaches the surfaced list.
    """

    from trader.config import BreakoutConfig, StrategyConfig
    from trader.runner import Runner

    run = Runner(sleep=lambda _s: None).run_analysis(
        _FakeMarket(_zigzag_frame()),  # type: ignore[arg-type]
        _NoTradingView(),  # type: ignore[arg-type]
        ["AAAUSDT"],
        ["4h", "1d"],
        reference_timeframe="4h",
        strategies=StrategyConfig(enabled=True),
        breakout=BreakoutConfig(enabled=True),
    )

    (analysis,) = run.analyses
    assert analysis.direction is Direction.NONE
    assert analysis.setup_direction is Direction.LONG
    assert [v.strategy for v in analysis.verdicts] == [None, StrategyId.BREAKOUT_LONG]
    assert analysis.plan is not None
    assert [s.symbol for s in run.setups] == ["AAAUSDT"]


def test_with_the_breakout_family_off_that_same_coin_does_not_surface() -> None:
    """The knob is real: nothing about the coin changed, only whether 2A/2B were judged."""

    from trader.config import BreakoutConfig, StrategyConfig
    from trader.runner import Runner

    run = Runner(sleep=lambda _s: None).run_analysis(
        _FakeMarket(_zigzag_frame()),  # type: ignore[arg-type]
        _NoTradingView(),  # type: ignore[arg-type]
        ["AAAUSDT"],
        ["4h", "1d"],
        reference_timeframe="4h",
        strategies=StrategyConfig(enabled=True),
        breakout=BreakoutConfig(enabled=False),
    )

    (analysis,) = run.analyses
    assert [v.strategy for v in analysis.verdicts] == [None]
    assert run.setups == ()
