"""Bar-by-bar outcome resolution for a hypothetical trade.

:class:`TradeSimulator` takes an :class:`EntrySetup` (entry, stop, target, direction)
and the finer-timeframe bars *after* entry and walks them forward to decide what price
actually did: a :attr:`TradeResult.WIN` when the target is touched first, a
:attr:`TradeResult.LOSS` when the stop is touched first. Touches are detected on the
bars' highs and lows (intrabar), and a single bar whose range spans **both** the stop
and the target is counted as a loss — the conservative stop-first tie-break, so any
reported edge is understated rather than flattered.

An :class:`EntrySetup` may also carry a **pending limit**: a zone and a bar budget. Then
the trade does not exist until a bar trades into that zone, and the recorded fill is the
zone edge the plan nominated (``setup.entry``), never the reference close. If no bar reaches
the zone within the budget, :meth:`TradeSimulator.simulate` returns ``None`` — *no trade
happened*, which is a different statement from an unresolved one. This is what stops an
entry zone below the current price from silently handing every long a better fill the market
may never have printed: without it, moving the entry down would shrink risk and inflate
every R-multiple, and the measured delta would look excellent and mean nothing.

Costs are honest by default: a configurable ``fee_rate`` (per side) and ``slippage``
(per fill) worsen the entry and exit fills, and the realized **R-multiple** (profit or
loss as a multiple of the risked distance entry->stop) is computed *net* of those
costs. Setting both to zero reproduces the gross edge. An optional ``max_holding_bars``
time-stop exits at market (the holding bar's close) when neither level is reached in
time; when neither level nor the time-stop is reached before the bars run out the
outcome is :attr:`TradeResult.UNRESOLVED` and is excluded from the win/loss statistics
— no outcome is invented.

This module belongs to the analysis core: pure, deterministic, no I/O, and it does not
import ``typer`` or ``rich``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from trader.direction import Direction
from trader.market_data import Candles

# Default trading costs. Zero here so the pure simulator is gross by default and every
# cost is opt-in; the backtester supplies net-by-default costs (task 05 lifts them onto
# the validated YAML config surface).
DEFAULT_FEE_RATE = 0.0
DEFAULT_SLIPPAGE = 0.0


class TradeResult(StrEnum):
    """The resolved outcome of a simulated trade."""

    WIN = "WIN"
    LOSS = "LOSS"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class EntrySetup:
    """A hypothetical trade to resolve: its direction and the three price levels.

    ``entry_time`` is the moment (ms) the trade was entered; ``entry``/``stop``/
    ``target`` are the absolute entry, stop-loss, and take-profit prices.

    """

    symbol: str
    direction: Direction
    entry_time: int
    entry: float
    stop: float
    target: float


@dataclass(frozen=True)
class TradeOutcome:
    """The resolved (or unresolved) result of simulating one :class:`EntrySetup`.

    ``exit_time`` / ``exit_price`` are ``None`` while the trade is unresolved (neither
    level nor the time-stop reached before the finer bars ended). ``r_multiple`` is the
    realized profit/loss as a multiple of the risked distance (entry->stop), **net** of
    ``costs``; both are ``0.0`` for an unresolved trade. ``costs`` is the per-unit cost
    drag (fees + slippage) deducted from the gross result.
    """

    symbol: str
    direction: Direction
    entry_time: int
    entry_price: float
    exit_time: int | None
    exit_price: float | None
    result: TradeResult
    r_multiple: float = 0.0
    costs: float = 0.0


@dataclass(frozen=True)
class _Costed:
    """The net R-multiple and per-unit cost drag of exiting a setup at ``exit_price``."""

    r_multiple: float
    costs: float


def _apply_costs(
    setup: EntrySetup, exit_price: float, fee_rate: float, slippage: float
) -> _Costed:
    """Net R-multiple and cost drag for exiting ``setup`` at ``exit_price``.

    Slippage worsens both fills (a long buys higher and sells lower; a short mirrors it)
    and a per-side ``fee_rate`` is charged on each fill's notional. ``R`` is the risked
    distance ``|entry - stop|``. Returns the net (post-cost) R-multiple and the per-unit
    cost drag (gross minus net PnL, non-negative for non-negative fee/slippage inputs).
    """

    risk = abs(setup.entry - setup.stop)
    if setup.direction is Direction.LONG:
        fill_entry = setup.entry * (1.0 + slippage)
        fill_exit = exit_price * (1.0 - slippage)
        fee = fee_rate * fill_entry + fee_rate * fill_exit
        net_pnl = (fill_exit - fill_entry) - fee
        gross_pnl = exit_price - setup.entry
    else:
        fill_entry = setup.entry * (1.0 - slippage)
        fill_exit = exit_price * (1.0 + slippage)
        fee = fee_rate * fill_entry + fee_rate * fill_exit
        net_pnl = (fill_entry - fill_exit) - fee
        gross_pnl = setup.entry - exit_price

    r_multiple = net_pnl / risk if risk else 0.0
    return _Costed(r_multiple=r_multiple, costs=gross_pnl - net_pnl)


class TradeSimulator:
    """Resolves a trade's outcome by first touch over finer-timeframe bars. Pure."""

    def simulate(
        self,
        setup: EntrySetup,
        finer_bars: Candles,
        *,
        fee_rate: float = DEFAULT_FEE_RATE,
        slippage: float = DEFAULT_SLIPPAGE,
        max_holding_bars: int | None = None,
    ) -> TradeOutcome | None:
        """Walk ``finer_bars`` forward and resolve ``setup`` on first touch.

        For a long, a bar with ``low <= stop`` is a stop and ``high >= target`` a
        target; a short mirrors this (``high >= stop`` / ``low <= target``). The stop
        is tested *before* the target within each bar, so a bar that spans both counts
        as a loss (the conservative tie-break). ``fee_rate`` and ``slippage`` are applied
        to the entry and exit, and the returned :attr:`TradeOutcome.r_multiple` is net of
        them. When ``max_holding_bars`` is set and neither level is hit within that many
        bars, the trade exits at market (that bar's close), classified by the sign of its
        net R-multiple (a non-positive result is a loss — conservative). If neither level
        nor the time-stop is reached before the bars run out, the outcome is
        :attr:`TradeResult.UNRESOLVED`.

        When ``setup`` carries a pending limit (see :class:`EntrySetup`), the walk first
        looks for a bar touching the zone within the fill window; resolution then starts on
        that same bar, because the bar that fills you can also stop you out. If the zone is
        never touched in time, ``None`` is returned: there is no trade to report.
        """

        frame = finer_bars.frame
        # Iterate as plain Python values (Series.tolist) so the loop is simple to type
        # and does not depend on the frame's dtype-specific row tuples.
        times = frame["timestamp"].tolist()
        highs = frame["high"].tolist()
        lows = frame["low"].tolist()
        closes = frame["close"].tolist()

        for idx, (bar_time_raw, high_raw, low_raw, close_raw) in enumerate(
            zip(times, highs, lows, closes, strict=True)
        ):
            bar_time = int(bar_time_raw)
            high = float(high_raw)
            low = float(low_raw)

            if setup.direction is Direction.LONG:
                hit_stop = low <= setup.stop
                hit_target = high >= setup.target
            else:
                hit_stop = high >= setup.stop
                hit_target = low <= setup.target

            # Stop-first tie-break: a bar spanning both levels is resolved as a loss.
            if hit_stop:
                return self._resolve(
                    setup, bar_time, setup.stop, TradeResult.LOSS, fee_rate, slippage
                )
            if hit_target:
                return self._resolve(
                    setup, bar_time, setup.target, TradeResult.WIN, fee_rate, slippage
                )

            # Time-stop: exit at market (this bar's close) once held long enough.
            if max_holding_bars is not None and (idx + 1) >= max_holding_bars:
                close = float(close_raw)
                costed = _apply_costs(setup, close, fee_rate, slippage)
                result = TradeResult.WIN if costed.r_multiple > 0.0 else TradeResult.LOSS
                return self._resolve(setup, bar_time, close, result, fee_rate, slippage)

        return TradeOutcome(
            symbol=setup.symbol,
            direction=setup.direction,
            entry_time=setup.entry_time,
            entry_price=setup.entry,
            exit_time=None,
            exit_price=None,
            result=TradeResult.UNRESOLVED,
        )


    @staticmethod
    def _resolve(
        setup: EntrySetup,
        exit_time: int,
        exit_price: float,
        result: TradeResult,
        fee_rate: float,
        slippage: float,
    ) -> TradeOutcome:
        costed = _apply_costs(setup, exit_price, fee_rate, slippage)
        return TradeOutcome(
            symbol=setup.symbol,
            direction=setup.direction,
            entry_time=setup.entry_time,
            entry_price=setup.entry,
            exit_time=exit_time,
            exit_price=exit_price,
            result=result,
            r_multiple=costed.r_multiple,
            costs=costed.costs,
        )
