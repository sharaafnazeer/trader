"""Concrete trade-plan computation for a surfaced setup.

:class:`TradePlanner` turns a decided :class:`~trader.direction.Direction`, the
reference timeframe's candles and detected structure, and its ATR into a
:class:`TradePlan`: a concrete entry, stop-loss, take-profit, and the resulting
risk-to-reward ratio, so the tool tells the trader not just *what* to trade but
*how*.

The plan is mechanical:

* **Entry** is the latest close.
* **Stop-loss** sits beyond the trade's structural invalidation level (the last
  swing low for a long, the last swing high for a short) padded by
  ``atr_buffer × ATR`` so the trade is stopped out only when the setup is genuinely
  wrong rather than on ordinary noise.
* **Take-profit** is the next structural pivot in the trade's favour (the next swing
  high above entry for a long, the next swing low below entry for a short), *floored*
  by ``target_rr``: if that pivot is too close to yield the target risk-to-reward, the
  target is pushed out to exactly the ``target_rr`` distance so the reward is always
  worthwhile.
* **Risk-to-reward** is ``reward / risk`` for the resulting levels.

A plan cannot be built without a usable invalidation pivot (or for a directionless
coin); in that case :meth:`TradePlanner.plan` returns ``None`` and the caller degrades
gracefully.

This module belongs to the analysis core: it is pure and deterministic, performs no
I/O, and does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

from dataclasses import dataclass

from trader.config import DEFAULT_ATR_BUFFER, DEFAULT_TARGET_RR
from trader.direction import Direction
from trader.market_data import Candles
from trader.structure import StructureState


@dataclass(frozen=True)
class TradePlan:
    """A concrete, mechanical trade plan for a surfaced setup.

    All prices are absolute. ``risk_reward`` is ``reward / risk`` for the levels; it is
    always at or above the configured target because the take-profit is floored by it.
    ``invalidation`` is the structural swing level the stop is placed beyond.
    """

    direction: Direction
    entry: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    invalidation: float


class TradePlanner:
    """Derives a :class:`TradePlan` from structure and ATR. Pure and deterministic."""

    def plan(
        self,
        direction: Direction,
        candles: Candles,
        pivots: StructureState,
        atr: float,
        *,
        atr_buffer: float = DEFAULT_ATR_BUFFER,
        target_rr: float = DEFAULT_TARGET_RR,
        invalidation: float | None = None,
    ) -> TradePlan | None:
        """Build a trade plan for ``direction`` from the reference timeframe.

        ``candles`` supplies the entry (latest close); ``pivots`` supplies the
        structural swing levels (its ``last_swing_low`` / ``last_swing_high`` is the
        invalidation and its ``swing_highs`` / ``swing_lows`` the candidate targets);
        ``atr`` is the reference timeframe's ATR. Returns ``None`` when the direction is
        ``NONE``, the invalidation pivot is missing, or the geometry is degenerate (a
        non-positive risk), so the caller can degrade rather than emit a bad plan.

        ``invalidation`` optionally overrides the structural swing level with an explicit
        level (e.g. a breakout level for the momentum scanner). When supplied the stop is
        still placed an ``atr_buffer × ATR`` beyond it — below it for a long, above it for
        a short — so this is additive and leaves the default swing-based path unchanged.
        """

        if direction is Direction.NONE:
            return None

        entry = candles.latest_close

        if direction is Direction.LONG:
            level = invalidation if invalidation is not None else pivots.last_swing_low
            if level is None:
                return None
            stop_loss = level - atr_buffer * atr
            risk = entry - stop_loss
            if risk <= 0.0:
                return None
            take_profit = self._long_take_profit(entry, risk, target_rr, pivots.swing_highs)
            risk_reward = (take_profit - entry) / risk
        else:
            level = invalidation if invalidation is not None else pivots.last_swing_high
            if level is None:
                return None
            stop_loss = level + atr_buffer * atr
            risk = stop_loss - entry
            if risk <= 0.0:
                return None
            take_profit = self._short_take_profit(entry, risk, target_rr, pivots.swing_lows)
            risk_reward = (entry - take_profit) / risk

        return TradePlan(
            direction=direction,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=risk_reward,
            invalidation=level,
        )

    @staticmethod
    def _long_take_profit(
        entry: float, risk: float, target_rr: float, swing_highs: tuple[float, ...]
    ) -> float:
        """Next resistance above entry, floored so the reward is at least ``target_rr``."""

        floor = entry + target_rr * risk
        resistances = [high for high in swing_highs if high > entry]
        if not resistances:
            return floor
        # The nearest resistance is the natural target; if it is too close to yield the
        # target R:R, push the target out to the floor instead.
        return max(min(resistances), floor)

    @staticmethod
    def _short_take_profit(
        entry: float, risk: float, target_rr: float, swing_lows: tuple[float, ...]
    ) -> float:
        """Next support below entry, floored so the reward is at least ``target_rr``."""

        floor = entry - target_rr * risk
        supports = [low for low in swing_lows if low < entry]
        if not supports:
            return floor
        # Mirror of the long case: the nearest support is the natural target; a target
        # too close is pushed down to the floor for at least the target R:R.
        return min(max(supports), floor)
