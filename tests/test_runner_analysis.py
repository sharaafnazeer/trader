"""Integration tests for the v2 Runner.run_analysis pipeline with mocked providers.

Both data seams are replaced by fakes (no network). Assertions cover the produced
per-coin directions, per-source throttling delays (via a mocked sleep, not wall-
clock), graceful skipping of a coin whose market data cannot be fetched, and the
best-effort treatment of a failing TradingView provider.
"""

from __future__ import annotations

import logging

import pandas as pd

from trader.config import (
    DEFAULT_CATEGORY_WEIGHTS,
    DEFAULT_MAX_SPREAD,
    DEFAULT_MIN_DEPTH,
    DEFAULT_REFERENCE_TIMEFRAME,
    DEFAULT_RELATIVE_VOLUME_MULTIPLE,
    DEFAULT_TARGET_RR,
)
from trader.direction import DEFAULT_LEAD_TIMEFRAME, Direction, MarketContext, decide
from trader.indicators import compute_features
from trader.market_data import DEFAULT_OHLCV_LIMIT, Candles, OrderBook
from trader.provider import (
    BUY,
    NEUTRAL,
    SELL,
    STRONG_BUY,
    STRONG_SELL,
    TimeframeResult,
)
from trader.runner import CoinAnalysis, Runner
from trader.scoring import LABEL_VALUES
from trader.scoring_model import QualityScore, ScoringModel, surface
from trader.structure import analyze as analyze_structure


def _rising_candles(symbol: str, timeframe: str, rows: int = 40) -> Candles:
    data = []
    for i in range(rows):
        close = 100.0 + i
        data.append([i, close - 1.0, close + 1.0, close - 2.0, close, 1_000.0 + i])
    frame = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return Candles(symbol=symbol, timeframe=timeframe, frame=frame)


class FakeMarketData:
    """Fake MarketDataProvider returning canned candles/order book; records calls."""

    def __init__(self, fail_ohlcv_for: set[str] | None = None, reason: str = "no data") -> None:
        self._fail_ohlcv_for = fail_ohlcv_for or set()
        self._reason = reason
        self.ohlcv_calls: list[tuple[str, str, int]] = []
        self.order_book_calls: list[tuple[str, int]] = []

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        self.ohlcv_calls.append((symbol, timeframe, limit))
        if symbol in self._fail_ohlcv_for:
            raise RuntimeError(self._reason)
        return _rising_candles(symbol, timeframe)

    def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        self.order_book_calls.append((symbol, depth))
        return OrderBook(
            symbol=symbol, best_bid=100.0, best_ask=100.5, bid_depth=50.0, ask_depth=50.0
        )


class FakeTradingView:
    """Fake best-effort AnalysisProvider; optionally always raises.

    Records both per-coin ``get_analysis`` calls (which the batched scan must no longer
    make) and per-interval ``get_analysis_batch`` calls.
    """

    def __init__(self, raise_always: bool = False) -> None:
        self._raise_always = raise_always
        self.calls: list[tuple[str, str, str, str]] = []
        self.batch_calls: list[tuple[tuple[str, ...], str, str, str]] = []

    def get_analysis(
        self, symbol: str, exchange: str, screener: str, interval: str
    ) -> TimeframeResult:
        self.calls.append((symbol, exchange, screener, interval))
        if self._raise_always:
            raise RuntimeError("rate limited")
        return TimeframeResult(
            symbol=symbol, interval=interval, recommendation=NEUTRAL, buy=0, neutral=0, sell=0
        )

    def get_analysis_batch(
        self, symbols: list[str], exchange: str, screener: str, interval: str
    ) -> dict[str, TimeframeResult]:
        self.batch_calls.append((tuple(symbols), exchange, screener, interval))
        if self._raise_always:
            raise RuntimeError("rate limited")
        return {
            symbol: TimeframeResult(
                symbol=symbol, interval=interval, recommendation=NEUTRAL, buy=0, neutral=0, sell=0
            )
            for symbol in symbols
        }


def _shaped_candles(
    symbol: str, timeframe: str, kind: str, base: float, rows: int = 260
) -> Candles:
    """A cleanly bullish or bearish series (higher/lower highs+lows) from ``base``.

    Mirrors the proven candle shape used by the CLI-scan tests so the EMA stack and
    market structure resolve a clear direction; ``base`` shifts the price level so a
    given timeframe's frame is distinguishable from another's by its latest close.
    """

    closes = []
    price = base
    for i in range(rows):
        price += 1.0 if kind == "bull" else -1.0
        offset = {0: 0.0, 1: 3.0, 2: 5.0, 3: 3.0, 4: 0.0, 5: -2.0}[i % 6]
        closes.append(price + offset)
    data = [[i, c, c + 1.0, c - 1.0, c, 1_000.0 + i] for i, c in enumerate(closes)]
    frame = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return Candles(symbol=symbol, timeframe=timeframe, frame=frame)


class SpotFakeMarketData:
    """Fake provider returning a per-symbol trend and per-timeframe price level.

    ``kinds`` maps a symbol to ``"bull"``/``"bear"``; ``bases`` maps a timeframe to
    its price base so the weekly frame's latest close differs from the monthly one,
    letting a test prove which timeframe supplied the trade-plan levels.
    """

    def __init__(self, kinds: dict[str, str], bases: dict[str, float]) -> None:
        self._kinds = kinds
        self._bases = bases
        self.ohlcv_calls: list[tuple[str, str, int]] = []
        self.order_book_calls: list[tuple[str, int]] = []

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        self.ohlcv_calls.append((symbol, timeframe, limit))
        return _shaped_candles(
            symbol, timeframe, self._kinds.get(symbol, "bull"), self._bases[timeframe]
        )

    def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        self.order_book_calls.append((symbol, depth))
        return OrderBook(
            symbol=symbol, best_bid=100.0, best_ask=100.5, bid_depth=50.0, ask_depth=50.0
        )


_SPOT_BASES = {"1d": 500.0, "1w": 600.0, "1M": 700.0}


def test_spot_profile_drives_weekly_monthly_evaluation_and_weekly_levels() -> None:
    # Spot profile: analyze 1d/1w/1M, lead 1M, higher timeframes 1w+1M, levels from 1w.
    market = SpotFakeMarketData(kinds={"ALT": "bull", "BTC/USDT": "bull"}, bases=_SPOT_BASES)
    tv = FakeTradingView()

    result = Runner(sleep=lambda _: None).run_analysis(
        market_data=market,
        analysis=tv,
        watchlist=["ALT"],
        timeframes=["1d", "1w", "1M"],
        lead_timeframe="1M",
        htf_timeframes=("1w", "1M"),
        reference_timeframe="1w",
        quality_threshold=0.0,
    )

    alt_tfs = {tf for (sym, tf, _) in market.ohlcv_calls if sym == "ALT"}
    # Weekly and monthly candles were requested; the futures-only 4h/15m/1h were not.
    assert {"1w", "1M"} <= alt_tfs
    assert "4h" not in alt_tfs and "15m" not in alt_tfs and "1h" not in alt_tfs

    (analysis,) = result.analyses
    # The lead (1M) resolved a direction and the 1w filter did not oppose it.
    assert analysis.direction is Direction.LONG
    assert analysis.reason is None
    # Trade levels are drawn from the 1w frame (its distinctive latest close), not 1M's.
    weekly_close = _shaped_candles("ALT", "1w", "bull", _SPOT_BASES["1w"]).latest_close
    monthly_close = _shaped_candles("ALT", "1M", "bull", _SPOT_BASES["1M"]).latest_close
    assert analysis.plan is not None
    assert analysis.plan.entry == weekly_close
    assert analysis.plan.entry != monthly_close


def test_spot_profile_computes_btc_context_on_profile_timeframes() -> None:
    # BTC's regime is judged on the spot timeframes (1w/1M): a bearish BTC vetoes an
    # otherwise-bullish altcoin, proving the context was computed on the profile set.
    market = SpotFakeMarketData(kinds={"ALT": "bull", "BTC/USDT": "bear"}, bases=_SPOT_BASES)
    tv = FakeTradingView()

    result = Runner(sleep=lambda _: None).run_analysis(
        market_data=market,
        analysis=tv,
        watchlist=["ALT"],
        timeframes=["1d", "1w", "1M"],
        lead_timeframe="1M",
        htf_timeframes=("1w", "1M"),
        reference_timeframe="1w",
        quality_threshold=0.0,
    )

    btc_tfs = {tf for (sym, tf, _) in market.ohlcv_calls if sym == "BTC/USDT"}
    # BTC context fetched on the profile timeframes, not the futures 4h/1d-only set.
    assert {"1w", "1M"} <= btc_tfs
    assert "4h" not in btc_tfs

    (analysis,) = result.analyses
    assert analysis.direction is Direction.NONE
    assert analysis.reason == "btc_veto"


class ShortHistoryMarketData:
    """Fake provider returning short (limited-history) shaped candles for every coin.

    ``rows`` is below the slow-EMA window, so ``compute_features`` degrades and flags
    ``limited_history`` — letting a test prove the marker propagates and the coin is
    still evaluated (not dropped).
    """

    def __init__(self, kind: str = "bull", rows: int = 120) -> None:
        self._kind = kind
        self._rows = rows

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        return _shaped_candles(symbol, timeframe, self._kind, 100.0, rows=self._rows)

    def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        return OrderBook(
            symbol=symbol, best_bid=100.0, best_ask=100.5, bid_depth=50.0, ask_depth=50.0
        )


def test_limited_history_coin_is_still_evaluated_surfaces_and_marks_the_result() -> None:
    # 120-row frames are too short for the slow EMA: the coin degrades to a fallback
    # stack. It must still resolve a direction (not be dropped) and carry the marker.
    market = ShortHistoryMarketData(kind="bull", rows=120)
    tv = FakeTradingView()

    result = Runner(sleep=lambda _: None).run_analysis(
        market_data=market,
        analysis=tv,
        watchlist=["YOUNG"],
        timeframes=["4h", "1d"],
        quality_threshold=0.0,
    )

    (analysis,) = result.analyses
    assert analysis.limited_history is True
    # Still evaluated and surfaceable: a decided direction with no gating reason.
    assert analysis.direction is Direction.LONG
    assert analysis.reason is None
    assert analysis.symbol in {s.symbol for s in result.setups}


def test_full_history_coin_is_not_marked_limited_history() -> None:
    market = ShortHistoryMarketData(kind="bull", rows=260)
    tv = FakeTradingView()

    result = Runner(sleep=lambda _: None).run_analysis(
        market_data=market,
        analysis=tv,
        watchlist=["MATURE"],
        timeframes=["4h", "1d"],
        quality_threshold=0.0,
    )

    (analysis,) = result.analyses
    assert analysis.limited_history is False


def test_produces_a_direction_for_each_successfully_fetched_coin() -> None:
    market = FakeMarketData()
    tv = FakeTradingView()

    result = Runner(sleep=lambda _: None).run_analysis(
        market_data=market,
        analysis=tv,
        watchlist=["AAA", "BBB"],
        timeframes=["4h", "1d"],
    )

    assert [a.symbol for a in result.analyses] == ["AAA", "BBB"]
    assert result.failures == ()
    for analysis in result.analyses:
        assert isinstance(analysis.direction, Direction)
        assert set(analysis.features_by_tf) == {"4h", "1d"}
        assert set(analysis.structure_by_tf) == {"4h", "1d"}
        assert analysis.order_book.symbol == analysis.symbol


def test_per_source_sleeps_use_the_configured_binance_and_tradingview_delays() -> None:
    market = FakeMarketData()
    tv = FakeTradingView()
    sleeps: list[float] = []

    Runner(sleep=sleeps.append).run_analysis(
        market_data=market,
        analysis=tv,
        watchlist=["AAA", "BBB"],
        timeframes=["4h", "1d"],
        binance_delay=0.2,
        tradingview_delay=1.5,
    )

    # Binance requests: BTC context (2 ohlcv) + per coin (2 ohlcv + 1 order book) x 2
    # = 8; sleeps skip only the very first Binance request -> 7 at the Binance delay.
    assert len(market.ohlcv_calls) == 6  # 2 (BTC) + 2 + 2
    assert len(market.order_book_calls) == 2
    # TradingView requests: one batch per timeframe (2), not per coin; the first TV
    # request skips the sleep -> 1 sleep at the TV delay. Zero per-coin get_analysis.
    assert len(tv.batch_calls) == 2
    assert tv.calls == []
    assert sleeps.count(0.2) == 7
    assert sleeps.count(1.5) == 1
    # No other delay values were used.
    assert set(sleeps) == {0.2, 1.5}


def test_coin_whose_market_data_fails_is_skipped_and_recorded() -> None:
    market = FakeMarketData(fail_ohlcv_for={"BBB"}, reason="network down")
    tv = FakeTradingView()

    result = Runner(sleep=lambda _: None).run_analysis(
        market_data=market,
        analysis=tv,
        watchlist=["AAA", "BBB", "CCC"],
        timeframes=["4h", "1d"],
    )

    assert [a.symbol for a in result.analyses] == ["AAA", "CCC"]
    assert [(f.symbol, f.reason) for f in result.failures] == [("BBB", "network down")]
    # The failed coin never reached the TradingView source.
    assert not any(call[0] == "BBB" for call in tv.calls)


def test_tradingview_failure_does_not_skip_the_coin() -> None:
    market = FakeMarketData()
    tv = FakeTradingView(raise_always=True)

    result = Runner(sleep=lambda _: None).run_analysis(
        market_data=market,
        analysis=tv,
        watchlist=["AAA"],
        timeframes=["4h", "1d"],
    )

    # TradingView raised for every interval batch, yet the coin is still analyzed
    # (degraded to no TradingView contribution); one batch attempted per timeframe.
    assert [a.symbol for a in result.analyses] == ["AAA"]
    assert result.failures == ()
    assert len(tv.batch_calls) == 2
    assert tv.calls == []


# --- Batched TradingView fetch (Task 01) --------------------------------------------


class TableTradingView:
    """Batch AnalysisProvider driven by a fixed ``{(symbol, interval): recommendation}``.

    Records batch calls and asserts the removed per-coin ``get_analysis`` is never used.
    ``fail_intervals`` makes ``get_analysis_batch`` raise for those intervals (to test
    whole-interval degradation); a ``(symbol, interval)`` absent from the table is
    omitted from that interval's response (to test a symbol missing from one interval).
    """

    def __init__(
        self,
        table: dict[tuple[str, str], str],
        fail_intervals: set[str] | None = None,
    ) -> None:
        self._table = table
        self._fail_intervals = fail_intervals or set()
        self.calls: list[tuple[str, str, str, str]] = []
        self.batch_calls: list[tuple[tuple[str, ...], str, str, str]] = []

    def get_analysis(
        self, symbol: str, exchange: str, screener: str, interval: str
    ) -> TimeframeResult:
        self.calls.append((symbol, exchange, screener, interval))
        raise AssertionError("batched scan must not call per-coin get_analysis")

    def get_analysis_batch(
        self, symbols: list[str], exchange: str, screener: str, interval: str
    ) -> dict[str, TimeframeResult]:
        self.batch_calls.append((tuple(symbols), exchange, screener, interval))
        if interval in self._fail_intervals:
            raise RuntimeError("rate limited")
        out: dict[str, TimeframeResult] = {}
        for symbol in symbols:
            rec = self._table.get((symbol, interval))
            if rec is None:
                continue
            out[symbol] = TimeframeResult(
                symbol=symbol, interval=interval, recommendation=rec, buy=0, neutral=0, sell=0
            )
        return out


_EQUIV_BASES = {"4h": 300.0, "1d": 400.0}


def _btc_context(market: SpotFakeMarketData, timeframes: list[str]) -> MarketContext:
    """Recompute the once-per-run BTC market context exactly as ``run_analysis`` does."""
    btc = {tf: market.get_ohlcv("BTC/USDT", tf, DEFAULT_OHLCV_LIMIT) for tf in timeframes}
    feats = {tf: compute_features(c) for tf, c in btc.items()}
    structs = {tf: analyze_structure(c) for tf, c in btc.items()}
    direction = decide(
        feats,
        structs,
        MarketContext(),
        htf_timeframes=tuple(timeframes[-2:]),
        lead_timeframe=DEFAULT_LEAD_TIMEFRAME,
        require_confirmation=False,
        btc_veto=True,
    ).direction
    return MarketContext(btc_direction=direction)


def _reference_score(
    analysis: CoinAnalysis, tv_value: float | None, context: MarketContext
) -> QualityScore:
    """Score a coin from its own runner-produced inputs, overriding only ``tradingview``.

    Uses the runner's default scoring parameters (from config) and the exact
    features/structure/order book the runner built, so a match proves the runner fed
    ``tv_value`` and left every other factor unchanged.
    """
    return ScoringModel().score(
        symbol=analysis.symbol,
        direction=analysis.direction,
        features_by_tf=analysis.features_by_tf,
        structure_by_tf=analysis.structure_by_tf,
        btc_context=context,
        weights=dict(DEFAULT_CATEGORY_WEIGHTS),
        tradingview=tv_value,
        order_book=analysis.order_book,
        close_by_tf={tf: c.latest_close for tf, c in analysis.candles_by_tf.items()},
        relative_volume_multiple=DEFAULT_RELATIVE_VOLUME_MULTIPLE,
        min_depth=DEFAULT_MIN_DEPTH,
        max_spread=DEFAULT_MAX_SPREAD,
        risk_reward=analysis.plan.risk_reward if analysis.plan is not None else None,
        target_rr=DEFAULT_TARGET_RR,
    )


def test_batch_fetch_called_once_per_interval_and_never_per_coin() -> None:
    market = FakeMarketData()
    watchlist = ["AAA", "BBB", "CCC"]
    timeframes = ["4h", "1d"]
    table = {(s, tf): NEUTRAL for s in watchlist for tf in timeframes}
    tv = TableTradingView(table)

    Runner(sleep=lambda _: None).run_analysis(
        market_data=market,
        analysis=tv,
        watchlist=watchlist,
        timeframes=timeframes,
    )

    # Exactly one batch per configured timeframe (== T), and zero per-coin calls.
    assert [c[3] for c in tv.batch_calls] == timeframes
    assert tv.calls == []
    # Every batch covered the whole watchlist in a single request.
    for symbols, _exchange, _screener, _interval in tv.batch_calls:
        assert symbols == tuple(watchlist)


def test_scan_scoring_equivalence_from_a_fixed_recommendation_table() -> None:
    market = SpotFakeMarketData(
        kinds={"AAA": "bull", "BBB": "bull", "BTC/USDT": "bull"}, bases=_EQUIV_BASES
    )
    timeframes = ["4h", "1d"]
    # A fixed per-(symbol, interval) recommendation table drives the batch fake.
    table = {
        ("AAA", "4h"): STRONG_BUY,
        ("AAA", "1d"): BUY,
        ("BBB", "4h"): SELL,
        ("BBB", "1d"): STRONG_SELL,
    }
    tv = TableTradingView(table)

    result = Runner(sleep=lambda _: None).run_analysis(
        market_data=market,
        analysis=tv,
        watchlist=["AAA", "BBB"],
        timeframes=timeframes,
        reference_timeframe=DEFAULT_REFERENCE_TIMEFRAME,
        quality_threshold=0.0,
    )

    context = _btc_context(market, timeframes)
    expected_scores: dict[str, QualityScore] = {}
    for analysis in result.analyses:
        # tv_value is the mean of LABEL_VALUES over the coin's configured intervals.
        expected_tv = sum(LABEL_VALUES[table[(analysis.symbol, tf)]] for tf in timeframes) / len(
            timeframes
        )
        expected = _reference_score(analysis, expected_tv, context)
        expected_scores[analysis.symbol] = expected
        assert analysis.score is not None
        assert analysis.score == expected

    # A distinctive, non-zero tv_value was actually exercised per coin.
    assert (
        sum(LABEL_VALUES[table[("AAA", tf)]] for tf in timeframes) / len(timeframes) == 1.5
    )
    assert (
        sum(LABEL_VALUES[table[("BBB", tf)]] for tf in timeframes) / len(timeframes) == -1.5
    )

    # Surfaced setups match those computed directly from the table-derived scores.
    expected_setups = surface(list(expected_scores.values()), 0.0)
    assert [s.symbol for s in result.setups] == [s.symbol for s in expected_setups]
    assert [s.total for s in result.setups] == [s.total for s in expected_setups]


def test_whole_interval_batch_failure_degrades_that_interval_for_every_coin(
    caplog,
) -> None:
    market = SpotFakeMarketData(
        kinds={"AAA": "bull", "BBB": "bull", "BTC/USDT": "bull"}, bases=_EQUIV_BASES
    )
    timeframes = ["4h", "1d"]
    table = {
        ("AAA", "4h"): STRONG_BUY,
        ("AAA", "1d"): BUY,
        ("BBB", "4h"): SELL,
        ("BBB", "1d"): STRONG_SELL,
    }
    # The whole "4h" batch fails; "1d" succeeds.
    tv = TableTradingView(table, fail_intervals={"4h"})

    with caplog.at_level(logging.WARNING):
        result = Runner(sleep=lambda _: None).run_analysis(
            market_data=market,
            analysis=tv,
            watchlist=["AAA", "BBB"],
            timeframes=timeframes,
            quality_threshold=0.0,
        )

    # Exactly one warning, naming the degraded interval.
    warnings = [
        r for r in caplog.records if "TradingView batch degraded for interval 4h" in r.getMessage()
    ]
    assert len(warnings) == 1
    # Scan completes for every coin.
    assert [a.symbol for a in result.analyses] == ["AAA", "BBB"]

    # Every coin's tv contribution comes from the surviving "1d" interval only.
    context = _btc_context(market, timeframes)
    for analysis in result.analyses:
        expected_tv = float(LABEL_VALUES[table[(analysis.symbol, "1d")]])
        assert analysis.score is not None
        assert analysis.score == _reference_score(analysis, expected_tv, context)


def test_symbol_missing_from_one_interval_loses_only_its_own_contribution(caplog) -> None:
    market = SpotFakeMarketData(
        kinds={"AAA": "bull", "BBB": "bull", "BTC/USDT": "bull"}, bases=_EQUIV_BASES
    )
    timeframes = ["4h", "1d"]
    # BBB is absent from the "4h" response; AAA is present in both intervals.
    table = {
        ("AAA", "4h"): STRONG_BUY,
        ("AAA", "1d"): BUY,
        ("BBB", "1d"): STRONG_SELL,
    }
    tv = TableTradingView(table)

    with caplog.at_level(logging.WARNING):
        result = Runner(sleep=lambda _: None).run_analysis(
            market_data=market,
            analysis=tv,
            watchlist=["AAA", "BBB"],
            timeframes=timeframes,
            quality_threshold=0.0,
        )

    # A missing symbol in one interval is not a degradation warning.
    assert not any("degraded for interval" in r.getMessage() for r in caplog.records)

    context = _btc_context(market, timeframes)
    by_symbol = {a.symbol: a for a in result.analyses}

    # AAA unaffected: both intervals contribute.
    aaa = by_symbol["AAA"]
    aaa_tv = sum(LABEL_VALUES[table[("AAA", tf)]] for tf in timeframes) / len(timeframes)
    assert aaa.score is not None
    assert aaa.score == _reference_score(aaa, aaa_tv, context)

    # BBB loses only its missing 4h contribution: tv from "1d" alone.
    bbb = by_symbol["BBB"]
    bbb_tv = float(LABEL_VALUES[table[("BBB", "1d")]])
    assert bbb.score is not None
    assert bbb.score == _reference_score(bbb, bbb_tv, context)


def test_scan_chunks_batch_requests_end_to_end_over_a_large_watchlist() -> None:
    """End-to-end: run_analysis with the real TradingViewProvider (injected counting
    fetch, batch_size=2) over a watchlist exceeding the cap chunks the requests
    ceil(N/size) times per interval, and every coin still gets scored."""
    from math import ceil
    from typing import Any

    from trader.provider import TradingViewProvider

    class _Analysis:
        def __init__(self, summary: dict[str, Any]) -> None:
            self.summary = summary

    watchlist = ["C0USDT", "C1USDT", "C2USDT", "C3USDT", "C4USDT"]  # 5 coins
    timeframes = ["4h", "1d"]
    batch_size = 2
    calls: list[tuple[str, tuple[str, ...]]] = []

    def counting_fetch(
        screener: str, interval: str, request_symbols: list[str]
    ) -> dict[str, Any]:
        calls.append((interval, tuple(request_symbols)))
        return {
            rs: _Analysis({"RECOMMENDATION": NEUTRAL, "BUY": 1, "SELL": 1, "NEUTRAL": 1})
            for rs in request_symbols
        }

    tv = TradingViewProvider(multi_fetch=counting_fetch, batch_size=batch_size)
    result = Runner(sleep=lambda _: None).run_analysis(
        market_data=FakeMarketData(),
        analysis=tv,
        watchlist=watchlist,
        timeframes=timeframes,
        quality_threshold=0.0,
    )

    expected_chunks = ceil(len(watchlist) / batch_size)  # ceil(5/2) = 3
    for tf in timeframes:
        tf_calls = [c for c in calls if c[0] == tf]
        assert len(tf_calls) == expected_chunks, (tf, len(tf_calls))
        covered = {s for _itv, syms in tf_calls for s in syms}
        assert covered == {f"BINANCE:{s}" for s in watchlist}

    # Every coin still made it into the scored analyses.
    assert {a.symbol for a in result.analyses} == set(watchlist)
