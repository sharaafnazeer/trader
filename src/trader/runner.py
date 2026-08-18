"""Orchestration.

The :class:`Runner` coordinates the analysis pipeline. The v1 :meth:`Runner.run`
walks the watchlist and, for each coin, fetches every configured timeframe through
the :class:`~trader.provider.AnalysisProvider`, sleeping a configurable delay between
requests so TradingView is queried politely, then scores and ranks the survivors.

The v2 :meth:`Runner.run_analysis` orchestrates the richer multi-factor pipeline: it
fetches Binance candles per timeframe plus an order-book snapshot through the
:class:`~trader.market_data.MarketDataProvider`, queries TradingView best-effort, and
computes per-timeframe features and structure, then decides a trade direction per
coin. BTC's regime is fetched once per run and injected as market context. Requests
are throttled *per data source* (TradingView and Binance have separate delays) using
the same injectable-sleep pattern, and a coin whose market data cannot be fetched is
skipped and recorded so one bad symbol never aborts the run.

This module belongs to the analysis core; it deliberately does not import ``typer``
or ``rich`` so the pipeline stays usable from any interface.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from trader.config import (
    DEFAULT_ATR_BUFFER,
    DEFAULT_CATEGORY_WEIGHTS,
    DEFAULT_MAX_SPREAD,
    DEFAULT_MIN_DEPTH,
    DEFAULT_QUALITY_THRESHOLD,
    DEFAULT_REFERENCE_TIMEFRAME,
    DEFAULT_RELATIVE_VOLUME_MULTIPLE,
    DEFAULT_TARGET_RR,
)
from trader.direction import (
    DEFAULT_LEAD_TIMEFRAME,
    Direction,
    MarketContext,
    decide,
)
from trader.indicators import TimeframeFeatures, compute_features
from trader.market_data import (
    DEFAULT_OHLCV_LIMIT,
    DEFAULT_ORDER_BOOK_DEPTH,
    Candles,
    MarketDataProvider,
    OrderBook,
)
from trader.provider import AnalysisProvider, TimeframeResult
from trader.ranking import Ranking, rank
from trader.scoring import LABEL_VALUES, ScoringEngine
from trader.scoring_model import QualityScore, ScoringModel, surface
from trader.structure import StructureState
from trader.structure import analyze as analyze_structure
from trader.trade_planner import TradePlan, TradePlanner

logger = logging.getLogger(__name__)

# Built-in defaults for the pre-configuration stage; replaced by YAML config later.
DEFAULT_EXCHANGE = "BINANCE"
DEFAULT_SCREENER = "crypto"
DEFAULT_WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
DEFAULT_TIMEFRAMES = ["15m", "1h", "4h", "1d"]
DEFAULT_WEIGHTS: dict[str, float] = {"15m": 1.0, "1h": 2.0, "4h": 3.0, "1d": 4.0}
DEFAULT_BUY_THRESHOLD = 5.0
DEFAULT_SELL_THRESHOLD = -5.0
DEFAULT_REQUEST_DELAY = 1.0

# Per-source throttling defaults. TradingView rate-limits aggressively so it is
# spaced out far more than Binance's public market-data endpoints. These live in code
# for this phase; task 07 lifts them onto the validated YAML config surface.
DEFAULT_BINANCE_DELAY = 0.25
DEFAULT_TRADINGVIEW_DELAY = 2.0

# BTC is the market-context reference, fetched once per run (ccxt unified symbol).
DEFAULT_BTC_SYMBOL = "BTC/USDT"

# Data-source identifiers used to key the per-source throttle.
_SOURCE_BINANCE = "binance"
_SOURCE_TRADINGVIEW = "tradingview"

SleepFn = Callable[[float], None]


def _aggregate_tradingview(results: list[TimeframeResult]) -> float | None:
    """Aggregate per-timeframe TradingView labels into one value in ``[-2, 2]``.

    Each label is mapped through :data:`~trader.scoring.LABEL_VALUES` and averaged.
    Returns ``None`` when no timeframe was fetched (all rate-limited), so the scoring
    model treats TradingView as absent rather than neutral.
    """

    if not results:
        return None
    values = [LABEL_VALUES.get(result.recommendation, 0) for result in results]
    return sum(values) / len(values)


@dataclass(frozen=True)
class Failure:
    """A symbol that could not be analyzed, together with why."""

    symbol: str
    reason: str


@dataclass(frozen=True)
class RunResult:
    """The outcome of a single analysis run.

    ``ranking`` contains only the coins that were fetched successfully across every
    configured timeframe. ``failures`` records each symbol that was skipped and why,
    so a single bad symbol degrades the run gracefully instead of aborting it.
    """

    ranking: Ranking
    failures: tuple[Failure, ...] = ()


@dataclass(frozen=True, eq=False)
class CoinAnalysis:
    """The per-coin output of the v2 pipeline for a successfully-fetched symbol.

    Alongside the decided :class:`~trader.direction.Direction`, it carries the
    computed features, structure, raw candles, and order book keyed/held for the
    downstream scoring and trade-planning tasks (fetched once per run). ``score`` is
    the coin's :class:`~trader.scoring_model.QualityScore` when a direction was
    decided, and ``None`` when the direction is ``NONE`` (a directionless coin is
    never scored). ``plan`` is the coin's :class:`~trader.trade_planner.TradePlan` when
    one could be computed for a decided direction, else ``None``. ``reason`` is the
    :class:`~trader.direction.DirectionResult` reason a coin was not surfaced (e.g.
    ``"lead_unresolved"``, ``"filter_opposed"``, ``"btc_veto"``), or ``None`` when it
    resolved a direction. ``limited_history`` is ``True`` when any of the coin's
    timeframes had too little history for the longest moving average and fell back to a
    shorter stack (see :class:`~trader.indicators.TimeframeFeatures`); it is a
    lower-confidence marker orthogonal to surfacing — a limited-history coin is still
    evaluated and can surface. ``eq=False`` because
    the wrapped :class:`~trader.market_data.Candles` frames are not scalar-comparable
    and identity is sufficient here.
    """

    symbol: str
    direction: Direction
    features_by_tf: dict[str, TimeframeFeatures]
    structure_by_tf: dict[str, StructureState]
    candles_by_tf: dict[str, Candles]
    order_book: OrderBook
    score: QualityScore | None = None
    plan: TradePlan | None = None
    reason: str | None = None
    limited_history: bool = False


@dataclass(frozen=True)
class AnalysisRun:
    """The outcome of a single v2 multi-factor analysis run.

    ``analyses`` holds one :class:`CoinAnalysis` per coin whose market data was
    fetched successfully; ``setups`` is the threshold-filtered, score-ranked short
    list (highest conviction first); ``failures`` records skipped symbols so one bad
    coin never aborts the run.
    """

    analyses: tuple[CoinAnalysis, ...]
    setups: tuple[QualityScore, ...] = ()
    failures: tuple[Failure, ...] = ()


class Runner:
    """Coordinates fetch -> score -> rank across a watchlist. Sequential and throttled."""

    def __init__(self, sleep: SleepFn = time.sleep) -> None:
        # Sleep is injectable so tests can assert throttling without wall-clock delays.
        self._sleep = sleep
        self._engine = ScoringEngine()
        self._model = ScoringModel()
        self._planner = TradePlanner()

    def run(
        self,
        provider: AnalysisProvider,
        watchlist: list[str],
        timeframes: list[str],
        weights: dict[str, float],
        buy_threshold: float,
        sell_threshold: float,
        request_delay: float,
        exchange: str = DEFAULT_EXCHANGE,
        screener: str = DEFAULT_SCREENER,
    ) -> RunResult:
        """Fetch every (symbol, timeframe) in sequence, score each coin, and rank them.

        Between every provider request the injected sleep is called with
        ``request_delay`` so requests are spaced out rather than issued all at once.
        """

        scores = []
        failures: list[Failure] = []
        first_request = True
        for symbol in watchlist:
            results: list[TimeframeResult] = []
            failed = False
            for interval in timeframes:
                if not first_request:
                    self._sleep(request_delay)
                first_request = False
                # A symbol that fails on any required timeframe is recorded as failed
                # and excluded from the ranking; the run continues with the next symbol.
                try:
                    results.append(
                        provider.get_analysis(symbol, exchange, screener, interval)
                    )
                except Exception as exc:  # noqa: BLE001 - any fetch error degrades gracefully
                    failures.append(Failure(symbol=symbol, reason=str(exc) or repr(exc)))
                    failed = True
                    break
            if failed:
                continue
            scores.append(self._engine.score(symbol, results, weights))

        ranking = rank(scores, buy_threshold, sell_threshold)
        return RunResult(ranking=ranking, failures=tuple(failures))

    def run_analysis(
        self,
        market_data: MarketDataProvider,
        analysis: AnalysisProvider,
        watchlist: list[str],
        timeframes: list[str],
        binance_delay: float = DEFAULT_BINANCE_DELAY,
        tradingview_delay: float = DEFAULT_TRADINGVIEW_DELAY,
        ohlcv_limit: int = DEFAULT_OHLCV_LIMIT,
        order_book_depth: int = DEFAULT_ORDER_BOOK_DEPTH,
        exchange: str = DEFAULT_EXCHANGE,
        screener: str = DEFAULT_SCREENER,
        btc_symbol: str = DEFAULT_BTC_SYMBOL,
        category_weights: dict[str, float] | None = None,
        quality_threshold: float = DEFAULT_QUALITY_THRESHOLD,
        relative_volume_multiple: float = DEFAULT_RELATIVE_VOLUME_MULTIPLE,
        min_depth: float = DEFAULT_MIN_DEPTH,
        max_spread: float = DEFAULT_MAX_SPREAD,
        atr_buffer: float = DEFAULT_ATR_BUFFER,
        target_rr: float = DEFAULT_TARGET_RR,
        reference_timeframe: str = DEFAULT_REFERENCE_TIMEFRAME,
        long_only: bool = False,
        lead_timeframe: str = DEFAULT_LEAD_TIMEFRAME,
        htf_timeframes: tuple[str, ...] | None = None,
        require_confirmation: bool = False,
        btc_veto: bool = True,
    ) -> AnalysisRun:
        """Walk the watchlist through both data sources, decide direction, and score.

        TradingView is fetched up front in one batch per timeframe covering the whole
        watchlist (a whole-interval failure degrades that interval to no contribution,
        logged once). For each coin: fetch Binance candles for every timeframe plus one
        order-book snapshot, look up its pre-fetched TradingView recommendations (which
        are aggregated and folded into the score, degrading gracefully to no
        contribution when absent), compute features and structure per timeframe, decide a direction
        against the once-per-run BTC market context, and — when the direction is not
        ``NONE`` — score the coin 0-100 across the weighted categories. Requests are
        throttled per source via the injected sleep: ``binance_delay`` between Binance
        requests and ``tradingview_delay`` between TradingView requests, in each case
        skipping the sleep before that source's very first request. A coin whose market
        data cannot be fetched at all is recorded as a :class:`Failure` and skipped;
        the run continues with the next coin. The returned ``setups`` are the surfaced
        coins (direction and total ``>= quality_threshold``) ranked by total; when
        ``long_only`` is set, short setups are excluded from that short list.
        """

        weights = (
            category_weights if category_weights is not None else dict(DEFAULT_CATEGORY_WEIGHTS)
        )

        # The higher-timeframe subset the direction rule filters on. When not supplied
        # it follows the active timeframe set (its top two), matching the profile the
        # caller resolved; the lead is the highest of that subset.
        htfs = tuple(htf_timeframes) if htf_timeframes is not None else tuple(timeframes[-2:])

        # Per-source throttle: sleep the source's delay before every request except the
        # first one seen for that source, mirroring the v1 single-source pattern.
        seen_sources: set[str] = set()

        def wait(source: str, delay: float) -> None:
            if source in seen_sources:
                self._sleep(delay)
            else:
                seen_sources.add(source)

        def fetch_candles(symbol: str) -> dict[str, Candles]:
            candles_by_tf: dict[str, Candles] = {}
            for interval in timeframes:
                wait(_SOURCE_BINANCE, binance_delay)
                candles_by_tf[interval] = market_data.get_ohlcv(symbol, interval, ohlcv_limit)
            return candles_by_tf

        # BTC regime once per run; a failure here simply means "no veto" (best-effort).
        try:
            btc_candles = fetch_candles(btc_symbol)
            btc_features = {tf: compute_features(c) for tf, c in btc_candles.items()}
            btc_structure = {tf: analyze_structure(c) for tf, c in btc_candles.items()}
            btc_direction = decide(
                btc_features,
                btc_structure,
                MarketContext(),
                htf_timeframes=htfs,
                lead_timeframe=lead_timeframe,
                require_confirmation=require_confirmation,
                btc_veto=btc_veto,
            ).direction
        except Exception as exc:  # noqa: BLE001 - BTC context is best-effort; degrade to no veto
            logger.warning(
                "BTC market context unavailable (%s); degrading to no veto", exc
            )
            btc_direction = Direction.NONE
        context = MarketContext(btc_direction=btc_direction)

        logger.info("scanning %d coins across %d timeframes", len(watchlist), len(timeframes))

        # TradingView is fetched in one batch per timeframe covering the whole watchlist
        # (instead of once per coin per timeframe), throttling once per source between
        # interval batches. A whole-interval failure degrades that interval to no
        # contribution for any coin (logged once) rather than aborting the scan.
        tv_by_interval: dict[str, dict[str, TimeframeResult]] = {}
        for interval in timeframes:
            wait(_SOURCE_TRADINGVIEW, tradingview_delay)
            try:
                tv_by_interval[interval] = analysis.get_analysis_batch(
                    watchlist, exchange, screener, interval
                )
            except Exception as exc:  # noqa: BLE001 - TradingView is best-effort per interval
                logger.warning("TradingView batch degraded for interval %s: %s", interval, exc)
                tv_by_interval[interval] = {}

        analyses: list[CoinAnalysis] = []
        failures: list[Failure] = []
        for symbol in watchlist:
            logger.info("evaluating %s", symbol)
            # Market data is required: any fetch error skips the coin and is recorded.
            try:
                candles_by_tf = fetch_candles(symbol)
                wait(_SOURCE_BINANCE, binance_delay)
                order_book = market_data.get_order_book(symbol, order_book_depth)
            except Exception as exc:  # noqa: BLE001 - any market-data error degrades gracefully
                reason = str(exc) or repr(exc)
                logger.warning("skipping %s: %s", symbol, reason)
                failures.append(Failure(symbol=symbol, reason=reason))
                continue

            # TradingView was pre-fetched per interval above; this coin simply looks up
            # its per-interval results. A missing symbol in an interval (or a degraded
            # interval) contributes nothing, exactly as a rate-limited fetch did before.
            tv_results = [
                tv_by_interval[itv][symbol]
                for itv in timeframes
                if symbol in tv_by_interval.get(itv, {})
            ]
            tv_value = _aggregate_tradingview(tv_results)

            features_by_tf = {tf: compute_features(c) for tf, c in candles_by_tf.items()}
            structure_by_tf = {tf: analyze_structure(c) for tf, c in candles_by_tf.items()}
            # A coin is marked limited-history when any timeframe fell back to a shorter
            # EMA stack for lack of candles; it is still evaluated and can surface.
            limited_history = any(f.limited_history for f in features_by_tf.values())
            result = decide(
                features_by_tf,
                structure_by_tf,
                context,
                htf_timeframes=htfs,
                lead_timeframe=lead_timeframe,
                require_confirmation=require_confirmation,
                btc_veto=btc_veto,
            )
            direction = result.direction

            # A directionless coin is never scored or planned; only long/short coins
            # are. The trade plan is computed first so its risk-to-reward feeds the
            # Risk-to-reward scoring category for the full 100-point total.
            score = None
            plan = None
            if direction is not Direction.NONE:
                ref_candles = candles_by_tf.get(reference_timeframe)
                ref_structure = structure_by_tf.get(reference_timeframe)
                ref_features = features_by_tf.get(reference_timeframe)
                if (
                    ref_candles is not None
                    and ref_structure is not None
                    and ref_features is not None
                ):
                    plan = self._planner.plan(
                        direction,
                        ref_candles,
                        ref_structure,
                        ref_features.atr,
                        atr_buffer=atr_buffer,
                        target_rr=target_rr,
                    )

                close_by_tf = {tf: c.latest_close for tf, c in candles_by_tf.items()}
                score = self._model.score(
                    symbol=symbol,
                    direction=direction,
                    features_by_tf=features_by_tf,
                    structure_by_tf=structure_by_tf,
                    btc_context=context,
                    weights=weights,
                    tradingview=tv_value,
                    order_book=order_book,
                    close_by_tf=close_by_tf,
                    relative_volume_multiple=relative_volume_multiple,
                    min_depth=min_depth,
                    max_spread=max_spread,
                    risk_reward=plan.risk_reward if plan is not None else None,
                    target_rr=target_rr,
                )

            analyses.append(
                CoinAnalysis(
                    symbol=symbol,
                    direction=direction,
                    features_by_tf=features_by_tf,
                    structure_by_tf=structure_by_tf,
                    candles_by_tf=candles_by_tf,
                    order_book=order_book,
                    score=score,
                    plan=plan,
                    reason=result.reason,
                    limited_history=limited_history,
                )
            )

        scores = [a.score for a in analyses if a.score is not None]
        setups = surface(scores, quality_threshold)
        if long_only:
            setups = tuple(s for s in setups if s.direction is Direction.LONG)
        return AnalysisRun(
            analyses=tuple(analyses), setups=setups, failures=tuple(failures)
        )
