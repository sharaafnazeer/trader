"""Unit/integration tests for the Runner using a fake provider and mock sleep."""

from __future__ import annotations

from trader.provider import NEUTRAL, STRONG_BUY, STRONG_SELL, TimeframeResult
from trader.ranking import Signal
from trader.runner import Runner


class SelectiveFailingProvider:
    """Fake provider that raises for chosen (symbol, interval) pairs, else returns NEUTRAL."""

    def __init__(self, fail_on: set[tuple[str, str]], reason: str = "boom") -> None:
        # Set of (symbol, interval) pairs on which get_analysis raises.
        self._fail_on = fail_on
        self._reason = reason
        self.calls: list[tuple[str, str, str, str]] = []

    def get_analysis(
        self, symbol: str, exchange: str, screener: str, interval: str
    ) -> TimeframeResult:
        self.calls.append((symbol, exchange, screener, interval))
        if (symbol, interval) in self._fail_on:
            raise RuntimeError(self._reason)
        return TimeframeResult(
            symbol=symbol,
            interval=interval,
            recommendation=NEUTRAL,
            buy=0,
            neutral=0,
            sell=0,
        )


class RecordingProvider:
    """Fake AnalysisProvider that records calls and returns canned recommendations."""

    def __init__(self, recommendations: dict[str, str]) -> None:
        # Map of symbol -> recommendation label returned for every timeframe.
        self._recommendations = recommendations
        self.calls: list[tuple[str, str, str, str]] = []

    def get_analysis(
        self, symbol: str, exchange: str, screener: str, interval: str
    ) -> TimeframeResult:
        self.calls.append((symbol, exchange, screener, interval))
        return TimeframeResult(
            symbol=symbol,
            interval=interval,
            recommendation=self._recommendations.get(symbol, NEUTRAL),
            buy=0,
            neutral=0,
            sell=0,
        )


def test_produces_ranking_sorted_strongest_buy_to_strongest_sell() -> None:
    provider = RecordingProvider(
        {"AAA": STRONG_SELL, "BBB": NEUTRAL, "CCC": STRONG_BUY}
    )
    sleeps: list[float] = []

    result = Runner(sleep=sleeps.append).run(
        provider=provider,
        watchlist=["AAA", "BBB", "CCC"],
        timeframes=["15m", "1h"],
        weights={"15m": 1.0, "1h": 2.0},
        buy_threshold=1.0,
        sell_threshold=-1.0,
        request_delay=0.5,
    )

    assert [c.score.symbol for c in result.ranking.coins] == ["CCC", "BBB", "AAA"]
    assert result.ranking.coins[0].signal is Signal.BUY
    assert result.ranking.coins[-1].signal is Signal.SELL


def test_issues_one_call_per_symbol_timeframe_in_sequence() -> None:
    provider = RecordingProvider({})
    watchlist = ["AAA", "BBB"]
    timeframes = ["15m", "1h", "4h"]

    Runner(sleep=lambda _: None).run(
        provider=provider,
        watchlist=watchlist,
        timeframes=timeframes,
        weights={"15m": 1.0, "1h": 2.0, "4h": 3.0},
        buy_threshold=1.0,
        sell_threshold=-1.0,
        request_delay=0.5,
    )

    expected = [
        (symbol, "BINANCE", "crypto", interval)
        for symbol in watchlist
        for interval in timeframes
    ]
    assert provider.calls == expected


def test_sleep_is_called_with_configured_delay_between_requests() -> None:
    provider = RecordingProvider({})
    sleeps: list[float] = []

    Runner(sleep=sleeps.append).run(
        provider=provider,
        watchlist=["AAA", "BBB"],
        timeframes=["15m", "1h"],
        weights={"15m": 1.0, "1h": 2.0},
        buy_threshold=1.0,
        sell_threshold=-1.0,
        request_delay=0.5,
    )

    # 4 requests -> sleep between each of the last 3 (no sleep before the first).
    assert len(provider.calls) == 4
    assert sleeps == [0.5, 0.5, 0.5]


def test_exchange_and_screener_are_passed_through() -> None:
    provider = RecordingProvider({})

    Runner(sleep=lambda _: None).run(
        provider=provider,
        watchlist=["AAA"],
        timeframes=["15m"],
        weights={"15m": 1.0},
        buy_threshold=1.0,
        sell_threshold=-1.0,
        request_delay=0.0,
        exchange="KRAKEN",
        screener="america",
    )

    assert provider.calls == [("AAA", "KRAKEN", "america", "15m")]


def _run(provider: object, watchlist: list[str], timeframes: list[str]):
    return Runner(sleep=lambda _: None).run(
        provider=provider,  # type: ignore[arg-type]
        watchlist=watchlist,
        timeframes=timeframes,
        weights={tf: 1.0 for tf in timeframes},
        buy_threshold=1.0,
        sell_threshold=-1.0,
        request_delay=0.0,
    )


def test_one_failing_symbol_yields_partial_ranking_and_records_failure() -> None:
    # BBB fails on its first timeframe; AAA and CCC succeed.
    provider = SelectiveFailingProvider(fail_on={("BBB", "15m")})

    result = _run(provider, ["AAA", "BBB", "CCC"], ["15m", "1h"])

    ranked_symbols = {c.score.symbol for c in result.ranking.coins}
    assert ranked_symbols == {"AAA", "CCC"}
    assert [f.symbol for f in result.failures] == ["BBB"]
    assert result.failures[0].reason == "boom"


def test_symbol_failing_on_one_timeframe_is_excluded_and_reported() -> None:
    # AAA succeeds on 15m but fails on the second timeframe (1h): still excluded.
    provider = SelectiveFailingProvider(fail_on={("AAA", "1h")}, reason="network down")

    result = _run(provider, ["AAA", "BBB"], ["15m", "1h"])

    assert [c.score.symbol for c in result.ranking.coins] == ["BBB"]
    assert [(f.symbol, f.reason) for f in result.failures] == [("AAA", "network down")]


def test_failure_stops_fetching_remaining_timeframes_for_that_symbol() -> None:
    # AAA fails on the first timeframe, so the second is never requested for it.
    provider = SelectiveFailingProvider(fail_on={("AAA", "15m")})

    _run(provider, ["AAA"], ["15m", "1h", "4h"])

    assert ("AAA", "BINANCE", "crypto", "1h") not in provider.calls
    assert ("AAA", "BINANCE", "crypto", "4h") not in provider.calls
