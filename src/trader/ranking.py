"""Pure signal classification and ranking.

Given a list of :class:`~trader.scoring.CoinScore`, the ranker sorts coins from
strongest buy to strongest sell and tags each with a :class:`Signal` against the
configured buy/sell thresholds. It performs no I/O and is deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from trader.scoring import CoinScore


class Signal(StrEnum):
    """Actionability tag derived from a coin's weighted score."""

    BUY = "BUY"
    SELL = "SELL"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class RankedCoin:
    """A scored coin together with its signal tag."""

    score: CoinScore
    signal: Signal


@dataclass(frozen=True)
class Ranking:
    """Coins ordered from strongest buy at the top to strongest sell at the bottom."""

    coins: tuple[RankedCoin, ...]


def _classify(value: float, buy_threshold: float, sell_threshold: float) -> Signal:
    if value >= buy_threshold:
        return Signal.BUY
    if value <= sell_threshold:
        return Signal.SELL
    return Signal.NEUTRAL


def rank(
    scores: list[CoinScore],
    buy_threshold: float,
    sell_threshold: float,
) -> Ranking:
    """Sort ``scores`` descending by weighted score and tag each coin.

    A coin whose score is ``>= buy_threshold`` is tagged ``BUY``; one ``<=
    sell_threshold`` is tagged ``SELL``; anything strictly between is ``NEUTRAL``.
    """

    ordered = sorted(scores, key=lambda s: s.score, reverse=True)
    coins = tuple(
        RankedCoin(score=s, signal=_classify(s.score, buy_threshold, sell_threshold))
        for s in ordered
    )
    return Ranking(coins=coins)
