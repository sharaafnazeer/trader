"""Summary metrics over simulated trades — the honest edge report.

:func:`summarize` reduces a collection of :class:`~trader.trade_simulator.TradeOutcome`
values into a :class:`BacktestReport`: win rate, expectancy (mean R), profit factor,
average and largest win/loss (in R), resolved/unresolved counts, per-coin and
per-direction breakdowns, and a fixed-fractional equity curve (trades ordered by entry
time, risking a fixed fraction of running equity each trade) whose largest peak-to-trough
decline is the reported maximum drawdown.

Unresolved trades (still open when the finer bars ran out) are deliberately excluded from
every win/loss statistic and from the equity curve — no outcome is invented; they are
only counted separately.

This module belongs to the analysis core: it is pure and deterministic, performs no
I/O, and does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from trader.trade_simulator import TradeOutcome, TradeResult

# Default fraction of running equity risked per trade on the fixed-fractional equity
# curve. In-code default for now (task 05 lifts it onto the validated YAML config).
DEFAULT_RISK_PER_TRADE = 0.01


@dataclass(frozen=True)
class Breakdown:
    """Win/loss statistics for one partition of the trades (a coin or a direction)."""

    key: str
    resolved_trades: int
    wins: int
    losses: int
    win_rate: float
    expectancy: float


@dataclass(frozen=True)
class BacktestReport:
    """The full summary of a backtest run.

    ``resolved_trades`` counts trades that reached a win or loss; ``wins``/``losses`` are
    the split and ``win_rate`` is ``wins / resolved_trades`` in ``[0, 1]``.
    ``unresolved_trades`` counts trades still open when the data ended (excluded from the
    win/loss stats). ``expectancy`` is the mean R-multiple over resolved trades;
    ``profit_factor`` is gross winning R over gross losing R (``inf`` when there are wins
    but no losing R). ``avg_win``/``avg_loss`` and ``largest_win``/``largest_loss`` are in
    R. ``max_drawdown`` is the largest peak-to-trough decline (a fraction) of the
    ``equity_curve``, a fixed-fractional curve starting at ``1.0`` that risks
    ``risk_per_trade`` of running equity per resolved trade. ``per_coin``/``per_direction``
    partition the resolved trades.
    """

    resolved_trades: int
    wins: int
    losses: int
    win_rate: float
    unresolved_trades: int
    expectancy: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    max_drawdown: float
    equity_curve: tuple[float, ...]
    per_coin: tuple[Breakdown, ...]
    per_direction: tuple[Breakdown, ...]
    risk_per_trade: float


def _breakdown(key: str, resolved: list[TradeOutcome]) -> Breakdown:
    wins = sum(1 for o in resolved if o.result is TradeResult.WIN)
    losses = sum(1 for o in resolved if o.result is TradeResult.LOSS)
    count = wins + losses
    win_rate = wins / count if count else 0.0
    expectancy = sum(o.r_multiple for o in resolved) / count if count else 0.0
    return Breakdown(
        key=key,
        resolved_trades=count,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        expectancy=expectancy,
    )


def _equity_curve(resolved: list[TradeOutcome], risk_per_trade: float) -> tuple[float, ...]:
    """A fixed-fractional equity curve over ``resolved`` trades ordered by entry time.

    Starts at ``1.0`` and, per trade, adds ``equity * risk_per_trade * r_multiple`` to the
    running equity. Returns the equity after each trade, prefixed with the ``1.0`` start.
    """

    ordered = sorted(resolved, key=lambda o: o.entry_time)
    equity = 1.0
    curve = [equity]
    for outcome in ordered:
        equity += equity * risk_per_trade * outcome.r_multiple
        curve.append(equity)
    return tuple(curve)


def _max_drawdown(curve: tuple[float, ...]) -> float:
    """Largest peak-to-trough decline of ``curve`` as a non-negative fraction."""

    peak = curve[0] if curve else 0.0
    worst = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def summarize(
    outcomes: Iterable[TradeOutcome], *, risk_per_trade: float = DEFAULT_RISK_PER_TRADE
) -> BacktestReport:
    """Reduce ``outcomes`` into a full :class:`BacktestReport`.

    Only :attr:`~trader.trade_simulator.TradeResult.WIN`/``LOSS`` trades count toward the
    win/loss statistics, expectancy, profit factor, breakdowns, and equity curve;
    :attr:`~trader.trade_simulator.TradeResult.UNRESOLVED` trades are counted separately
    and never fabricated into a win or loss. Deterministic: identical inputs yield an
    identical report.
    """

    all_outcomes = list(outcomes)
    resolved = [o for o in all_outcomes if o.result is not TradeResult.UNRESOLVED]
    unresolved = len(all_outcomes) - len(resolved)

    wins = sum(1 for o in resolved if o.result is TradeResult.WIN)
    losses = sum(1 for o in resolved if o.result is TradeResult.LOSS)
    resolved_count = wins + losses
    win_rate = wins / resolved_count if resolved_count else 0.0

    win_rs = [o.r_multiple for o in resolved if o.result is TradeResult.WIN]
    loss_rs = [o.r_multiple for o in resolved if o.result is TradeResult.LOSS]

    expectancy = sum(o.r_multiple for o in resolved) / resolved_count if resolved_count else 0.0

    gross_profit = sum(win_rs)
    gross_loss = -sum(loss_rs)  # loss R-multiples are <= 0, so this is >= 0
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = float("inf") if gross_profit > 0 else 0.0

    avg_win = sum(win_rs) / len(win_rs) if win_rs else 0.0
    avg_loss = sum(loss_rs) / len(loss_rs) if loss_rs else 0.0
    largest_win = max(win_rs) if win_rs else 0.0
    largest_loss = min(loss_rs) if loss_rs else 0.0

    equity_curve = _equity_curve(resolved, risk_per_trade)
    max_drawdown = _max_drawdown(equity_curve)

    coins = sorted({o.symbol for o in resolved})
    per_coin = tuple(
        _breakdown(coin, [o for o in resolved if o.symbol == coin]) for coin in coins
    )
    directions = sorted({o.direction.value for o in resolved})
    per_direction = tuple(
        _breakdown(d, [o for o in resolved if o.direction.value == d]) for d in directions
    )

    return BacktestReport(
        resolved_trades=resolved_count,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        unresolved_trades=unresolved,
        expectancy=expectancy,
        profit_factor=profit_factor,
        avg_win=avg_win,
        avg_loss=avg_loss,
        largest_win=largest_win,
        largest_loss=largest_loss,
        max_drawdown=max_drawdown,
        equity_curve=equity_curve,
        per_coin=per_coin,
        per_direction=per_direction,
        risk_per_trade=risk_per_trade,
    )
