"""Unit tests for the pure SignalClassifier/Ranker (no network, deterministic)."""

from __future__ import annotations

from trader.ranking import Signal, rank
from trader.scoring import CoinScore


def _score(symbol: str, value: float) -> CoinScore:
    return CoinScore(symbol=symbol, score=value, breakdown=())


def test_ranks_descending_from_strongest_buy_to_strongest_sell() -> None:
    scores = [
        _score("MID", 0.0),
        _score("TOP", 8.0),
        _score("BOTTOM", -8.0),
    ]

    ranking = rank(scores, buy_threshold=5.0, sell_threshold=-5.0)

    assert [c.score.symbol for c in ranking.coins] == ["TOP", "MID", "BOTTOM"]


def test_thresholds_are_inclusive_at_boundaries() -> None:
    scores = [
        _score("ATBUY", 5.0),  # exactly at buy threshold -> BUY
        _score("BETWEEN", 4.999),  # strictly between -> NEUTRAL
        _score("ATSELL", -5.0),  # exactly at sell threshold -> SELL
    ]

    ranking = rank(scores, buy_threshold=5.0, sell_threshold=-5.0)
    by_symbol = {c.score.symbol: c.signal for c in ranking.coins}

    assert by_symbol["ATBUY"] is Signal.BUY
    assert by_symbol["BETWEEN"] is Signal.NEUTRAL
    assert by_symbol["ATSELL"] is Signal.SELL


def test_strictly_between_thresholds_is_neutral() -> None:
    scores = [_score("X", 0.0)]

    ranking = rank(scores, buy_threshold=1.0, sell_threshold=-1.0)

    assert ranking.coins[0].signal is Signal.NEUTRAL
