"""Higher-timeframe trade-direction decision.

:func:`decide` turns a coin's per-timeframe features and structure into a
:class:`DirectionResult` — a decided :class:`Direction` (``LONG`` / ``SHORT`` /
``NONE``) plus a short ``reason`` explaining why a coin was *not* surfaced. The rule
is a tunable **lead-timeframe** rule: the highest (lead) timeframe decides the
direction, and the other higher timeframe(s) only have to *not oppose* it (rather
than fully confirm it), so clean trends surface instead of being filtered into
silence. Set ``require_confirmation`` to tighten this to must-confirm. A per-run
:class:`MarketContext` carries BTC's own regime, which — while ``btc_veto`` is on —
can veto an opposing altcoin trade.

Within a single timeframe the EMA stack *drives* the direction; market structure
only *vetoes* when it clearly opposes the stack (a ``BROKEN`` or neutral structure is
allowed), so a choppy-but-trending market still produces a direction.

This module belongs to the analysis core: it is pure and deterministic, performs no
I/O, and does not import ``typer`` or ``rich``. Fast timeframes are intentionally
ignored here — direction is a higher-timeframe decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from trader.indicators import TimeframeFeatures
from trader.structure import Structure, StructureState

# The higher timeframes considered by the rule; the last is the default lead.
HTF_TIMEFRAMES: tuple[str, ...] = ("4h", "1d")

# The default lead (highest) timeframe: it decides the direction.
DEFAULT_LEAD_TIMEFRAME = "1d"


class Direction(StrEnum):
    """The decided trade direction for a coin."""

    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


@dataclass(frozen=True)
class DirectionResult:
    """The outcome of the direction decision.

    ``direction`` is the decided :class:`Direction`. ``reason`` is ``None`` when the
    coin resolved a direction and is surfaceable; otherwise it is a short machine
    string explaining why the coin was gated out — one of ``"lead_unresolved"`` (the
    lead timeframe had no direction), ``"filter_opposed"`` (another higher timeframe
    resolved the opposite direction), ``"filter_unconfirmed"`` (``require_confirmation``
    is on and a higher timeframe did not confirm the lead), or ``"btc_veto"`` (BTC's
    regime opposes the trade). The limited-history marker is tracked separately and is
    deliberately *not* a direction reason.
    """

    direction: Direction
    reason: str | None = None


@dataclass(frozen=True)
class MarketContext:
    """Per-run market context injected into the direction decision.

    ``btc_direction`` is BTC's own decided direction; it lets a falling market veto an
    otherwise-bullish altcoin (and vice versa) while the BTC veto is enabled.
    """

    btc_direction: Direction = Direction.NONE


def _ema_stack(features: TimeframeFeatures) -> Direction:
    """Direction implied by the EMA 20/50/200 stacking, or ``NONE`` if not stacked."""

    if features.ema20 > features.ema50 > features.ema200:
        return Direction.LONG
    if features.ema20 < features.ema50 < features.ema200:
        return Direction.SHORT
    return Direction.NONE


def _timeframe_direction(features: TimeframeFeatures, state: StructureState) -> Direction:
    """Direction of a single timeframe: the EMA stack drives it, structure only vetoes.

    The EMA 20/50/200 stack decides the candidate direction. Market structure vetoes
    that candidate only when it *clearly opposes* it — a bullish stack is vetoed by
    ``BEARISH`` structure and a bearish stack by ``BULLISH`` structure. A ``BROKEN`` or
    otherwise non-opposing structure is allowed, so a choppy-but-trending market still
    produces a direction.
    """

    stack = _ema_stack(features)
    if stack is Direction.LONG and state.structure is not Structure.BEARISH:
        return Direction.LONG
    if stack is Direction.SHORT and state.structure is not Structure.BULLISH:
        return Direction.SHORT
    return Direction.NONE


def _resolve(
    features_by_tf: dict[str, TimeframeFeatures],
    structure_by_tf: dict[str, StructureState],
    timeframe: str,
) -> Direction:
    """Resolve a single timeframe's direction, or ``NONE`` if it is absent."""

    features = features_by_tf.get(timeframe)
    state = structure_by_tf.get(timeframe)
    if features is None or state is None:
        return Direction.NONE
    return _timeframe_direction(features, state)


def decide(
    features_by_tf: dict[str, TimeframeFeatures],
    structure_by_tf: dict[str, StructureState],
    btc_context: MarketContext,
    *,
    htf_timeframes: tuple[str, ...] = HTF_TIMEFRAMES,
    lead_timeframe: str = DEFAULT_LEAD_TIMEFRAME,
    require_confirmation: bool = False,
    btc_veto: bool = True,
) -> DirectionResult:
    """Decide a coin's direction from its higher timeframes and the BTC context.

    The lead timeframe (``lead_timeframe``, the highest in the active set) decides the
    direction; if it resolves ``NONE`` the result is ``NONE`` / ``"lead_unresolved"``.
    Each other higher timeframe then only has to *not oppose* the lead: one resolving
    the opposite direction yields ``NONE`` / ``"filter_opposed"``. A neutral or absent
    filter is allowed unless ``require_confirmation`` is set, in which case any filter
    that does not match the lead yields ``NONE`` / ``"filter_unconfirmed"``. Finally,
    while ``btc_veto`` is on, an opposing BTC regime yields ``NONE`` / ``"btc_veto"``
    (BTC never vetoes its own row, whose context carries ``Direction.NONE``). When the
    lead resolves and nothing gates it out, the result is that direction with no
    reason.
    """

    lead_dir = _resolve(features_by_tf, structure_by_tf, lead_timeframe)
    if lead_dir is Direction.NONE:
        return DirectionResult(Direction.NONE, "lead_unresolved")

    opposite = Direction.SHORT if lead_dir is Direction.LONG else Direction.LONG

    for timeframe in htf_timeframes:
        if timeframe == lead_timeframe:
            continue
        filter_dir = _resolve(features_by_tf, structure_by_tf, timeframe)
        if filter_dir is opposite:
            return DirectionResult(Direction.NONE, "filter_opposed")
        if require_confirmation and filter_dir is not lead_dir:
            return DirectionResult(Direction.NONE, "filter_unconfirmed")

    if btc_veto and btc_context.btc_direction is opposite:
        return DirectionResult(Direction.NONE, "btc_veto")

    return DirectionResult(lead_dir, None)
