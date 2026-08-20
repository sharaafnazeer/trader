"""Orchestration.

The :class:`Runner` coordinates the analysis pipeline. :meth:`Runner.run_analysis`
orchestrates it: it
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from trader.breakout import evaluate_breakout
from trader.config import (
    DEFAULT_ATR_BUFFER,
    DEFAULT_REFERENCE_TIMEFRAME,
    DEFAULT_TARGET_RR,
    BreakoutConfig,
    StrategyConfig,
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
from trader.patterns import detect as detect_patterns
from trader.provider import AnalysisProvider, TimeframeResult
from trader.scoring import LABEL_VALUES
from trader.strategy import EntryStatus, SetupVerdict
from trader.strategy import evaluate as evaluate_strategy
from trader.structure import StructureState
from trader.structure import analyze as analyze_structure
from trader.trade_planner import TradePlan, TradePlanner
from trader.trendline import Trendline
from trader.trendline import fit as fit_trendline

logger = logging.getLogger(__name__)

# Built-in defaults for the pre-configuration stage; replaced by YAML config later.
DEFAULT_EXCHANGE = "BINANCE"
DEFAULT_SCREENER = "crypto"
DEFAULT_WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
DEFAULT_TIMEFRAMES = ["15m", "1h", "4h", "1d"]

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


def _setup_rank(analysis: CoinAnalysis) -> tuple[int, int, str]:
    """Rank key: most conditions met first, structural targets ahead of manufactured ones.

    The second term is the reward-to-risk *preference*: between two setups meeting the same
    conditions, the one aiming at a real swing level outranks the one aiming at a target the
    planner manufactured to reach the ratio. It orders; it never excludes — a coin at new
    highs has no overhead level by definition and must stay tradeable.
    """

    met = analysis.verdict.conditions_met if analysis.verdict is not None else 0
    structural = analysis.plan is not None and analysis.plan.target_is_structural
    return (-met, 0 if structural else 1, analysis.symbol)


def primary_verdict(verdicts: Sequence[SetupVerdict]) -> SetupVerdict | None:
    """The verdict that decides a coin's row, out of every strategy that judged it.

    A coin can present a trend-pullback setup and a breakout setup at once, and the two are
    evaluated independently on purpose — a breakout does not gate on the stack, so neither
    can stand in for the other. That leaves four cases, and the interesting one is the last:

    * **Nothing matched** — the first verdict is returned so its "no setup" reason survives.
    * **Nothing ready** — the checklist nearest to complete is returned, because that is the
      most informative WAIT: it names the condition the coin is actually short of.
    * **One ready, or several agreeing on direction** — the strongest is returned and the
      trade is taken.
    * **Several ready, disagreeing on direction** — *nothing* is taken. A coin one strategy
      reads as a long and another reads as a short is not a strong signal in either
      direction, it is a coin two methods disagree about, and picking a winner by rank would
      manufacture confidence the evidence does not support. The row is forced to WAIT and the
      reason names both setups, so the trader can look and decide for themselves.
    """

    if not verdicts:
        return None
    matched = [v for v in verdicts if v.strategy is not None]
    if not matched:
        return verdicts[0]

    ready = [v for v in matched if v.is_ready]
    if not ready:
        return max(matched, key=lambda v: v.conditions_met)

    strongest = max(ready, key=lambda v: v.conditions_met)
    if len({v.decision for v in ready}) == 1:
        return strongest

    disagreeing = ", ".join(
        sorted(f"{v.strategy.value} reads {v.decision.value}" for v in ready if v.strategy)
    )
    return replace(
        strongest,
        entry_status=EntryStatus.NOT_READY,
        decision=Direction.NONE,
        reason=(
            f"two setups disagree on direction ({disagreeing}) — neither is taken; "
            "a coin read both ways is not a strong signal, it is an unclear one"
        ),
    )


@dataclass(frozen=True)
class Failure:
    """A symbol that could not be analyzed, together with why."""

    symbol: str
    reason: str




@dataclass(frozen=True, eq=False)
class CoinAnalysis:
    """The per-coin output of the v2 pipeline for a successfully-fetched symbol.

    Alongside the decided :class:`~trader.direction.Direction`, it carries the
    computed features, structure, raw candles, and order book keyed/held for the
    downstream trade-planning and strategy tasks (fetched once per run).
    ``plan`` is the coin's :class:`~trader.trade_planner.TradePlan` when
    one could be computed for a decided direction, else ``None``. ``reason`` is the
    :class:`~trader.direction.DirectionResult` reason a coin was not surfaced (e.g.
    ``"lead_unresolved"``, ``"filter_opposed"``, ``"btc_veto"``), or ``None`` when it
    resolved a direction. ``limited_history`` is ``True`` when any of the coin's
    timeframes had too little history for the 200-period average (see
    :class:`~trader.indicators.TimeframeFeatures`); such a coin has no long-term filter to
    judge against and therefore resolves no direction, so the marker explains a silence
    rather than qualifying a setup. ``eq=False`` because the wrapped
    :class:`~trader.market_data.Candles` frames are not scalar-comparable and identity is
    sufficient here.

    ``tradingview`` is the aggregated third-party recommendation in ``[-2, 2]``, or ``None``
    when the fetch failed. Its only consumer was the retired quality score, so nothing reads
    it today; it is carried rather than discarded so the scan's existing fetch is not silently
    wasted, and so a later task can decide whether the analyst should see it.

    ``reaction_patterns`` are the candlestick reactions read on the reference timeframe's
    recent bars, carried so the analyst sees the same price action the checklist judged.

    ``verdicts`` holds one four-part answer — trend, setup, entry status, decision — per
    strategy family that produced one: the trend-pullback checklist (1A/1B) and the breakout
    family (2A/2B) are evaluated independently, and a coin can legitimately present both. The
    :attr:`verdict` property picks the one that decides the row, and is what surfacing and
    ordering are driven from; consumers wanting the full picture read ``verdicts``.
    """

    symbol: str
    direction: Direction
    features_by_tf: dict[str, TimeframeFeatures]
    structure_by_tf: dict[str, StructureState]
    candles_by_tf: dict[str, Candles]
    order_book: OrderBook
    tradingview: float | None = None
    verdicts: tuple[SetupVerdict, ...] = ()
    reaction_patterns: tuple[str, ...] = ()
    trendline: Trendline | None = None
    plan: TradePlan | None = None
    reason: str | None = None
    limited_history: bool = False

    @property
    def verdict(self) -> SetupVerdict | None:
        """The verdict that decides this coin's row — see :func:`primary_verdict`."""

        return primary_verdict(self.verdicts)

    @property
    def setup_direction(self) -> Direction:
        """The direction this coin would actually be traded in.

        Not always the trend rule's answer: a breakout resolves its own direction from the
        level it broke, and may do so on a coin the trend rule called directionless. Falls
        back to the trend direction when no setup matched, so the pullback path is unchanged.
        """

        decided = self.verdict
        if decided is not None and decided.strategy is not None:
            if decided.trend is not Direction.NONE:
                return decided.trend
        return self.direction


@dataclass(frozen=True)
class AnalysisRun:
    """The outcome of a single v2 multi-factor analysis run.

    ``analyses`` holds one :class:`CoinAnalysis` per coin whose market data was
    fetched successfully; ``setups`` holds those that resolved a direction *and* produced
    a usable trade plan, in watchlist order. There is deliberately no ranking here: the
    0-100 quality score that used to order this list was retired with the indicators
    behind it, and the strategy checklist that will order it arrives in a later task.
    ``failures`` records skipped symbols so one bad coin never aborts the run.
    ``btc_direction`` is the once-per-run BTC regime the
    direction rule was evaluated against — ``NONE`` both when BTC is genuinely
    directionless and when its context could not be fetched (a degraded run casts no
    veto), so it is market context rather than a data-quality signal.
    """

    analyses: tuple[CoinAnalysis, ...]
    setups: tuple[CoinAnalysis, ...] = ()
    failures: tuple[Failure, ...] = ()
    btc_direction: Direction = Direction.NONE


class Runner:
    """Coordinates fetch -> analyse -> judge across a watchlist. Sequential and throttled."""

    def __init__(self, sleep: SleepFn = time.sleep) -> None:
        # Sleep is injectable so tests can assert throttling without wall-clock delays.
        self._sleep = sleep
        self._planner = TradePlanner()

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
        atr_buffer: float = DEFAULT_ATR_BUFFER,
        target_rr: float = DEFAULT_TARGET_RR,
        reference_timeframe: str = DEFAULT_REFERENCE_TIMEFRAME,
        long_only: bool = False,
        lead_timeframe: str = DEFAULT_LEAD_TIMEFRAME,
        htf_timeframes: tuple[str, ...] | None = None,
        require_confirmation: bool = False,
        btc_veto: bool = True,
        strategies: StrategyConfig | None = None,
        breakout: BreakoutConfig | None = None,
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
        the run continues with the next coin. The returned ``setups`` are the coins that
        resolved a direction and produced a usable trade plan, in watchlist order; when
        ``long_only`` is set, short setups are excluded from that list.
        """

        # The higher-timeframe subset the direction rule filters on. When not supplied
        # it follows the active timeframe set (its top two), matching the profile the
        # caller resolved; the lead is the highest of that subset.
        catalogue = strategies if strategies is not None else StrategyConfig()
        breakouts = breakout if breakout is not None else BreakoutConfig()

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

            # A directionless coin is never planned; only long/short coins are.
            plan = None
            verdicts: list[SetupVerdict] = []
            ref_candles = candles_by_tf.get(reference_timeframe)
            ref_structure = structure_by_tf.get(reference_timeframe)
            ref_features = features_by_tf.get(reference_timeframe)
            has_reference = (
                ref_candles is not None
                and ref_structure is not None
                and ref_features is not None
            )
            if direction is not Direction.NONE and has_reference:
                assert ref_candles is not None and ref_features is not None
                assert ref_structure is not None
                plan = self._planner.plan(
                    direction,
                    ref_candles,
                    ref_structure,
                    ref_features.atr,
                    atr_buffer=atr_buffer,
                    target_rr=target_rr,
                )

            # The checklist is judged on the reference timeframe — the one the plan's levels
            # come from — so the verdict and the plan always describe the same trade. It runs
            # for every coin that reached that timeframe, including directionless ones, whose
            # verdict is the honest "no setup matched" rather than an absence.
            reaction_patterns: tuple[str, ...] = ()
            trendline: Trendline | None = None
            if catalogue.enabled and has_reference:
                assert ref_candles is not None and ref_features is not None
                assert ref_structure is not None
                found = detect_patterns(
                    ref_candles,
                    bars=catalogue.reaction_lookback,
                    wick_body_ratio=catalogue.pattern_wick_body_ratio,
                )
                reaction_patterns = tuple(p.value for p in found)
                # A long is drawn under the swing lows, a short over the swing highs: the
                # line the trade would lean on, not the one above it.
                pivots = (
                    list(zip(
                        ref_structure.swing_low_indices,
                        ref_structure.swing_lows,
                        strict=False,
                    ))
                    if direction is Direction.LONG
                    else list(zip(
                        ref_structure.swing_high_indices,
                        ref_structure.swing_highs,
                        strict=False,
                    ))
                )
                trendline = fit_trendline(
                    pivots,
                    len(ref_candles.frame) - 1,
                    min_touches=catalogue.trendline_min_touches,
                    min_r_squared=catalogue.trendline_min_r2,
                )
                verdicts.append(
                    evaluate_strategy(
                        symbol,
                        direction,
                        ref_features,
                        ref_structure,
                        ref_candles.latest_close,
                        patterns=found,
                        trendline=trendline,
                        trendline_tolerance_atr=catalogue.trendline_tolerance_atr,
                        max_extension_atr=catalogue.max_extension_atr,
                        stoch_oversold=catalogue.stoch_oversold,
                        stoch_overbought=catalogue.stoch_overbought,
                        cross_lookback=catalogue.cross_lookback,
                    )
                )

            # The breakout family is judged independently of the pullback checklist and of
            # the direction rule: it takes its direction from the level it broke, so it runs
            # for directionless coins too — a coin with no stack can still break a level.
            if breakouts.enabled and has_reference:
                assert ref_candles is not None and ref_features is not None
                assert ref_structure is not None
                verdicts.append(
                    evaluate_breakout(
                        symbol,
                        ref_candles,
                        ref_features,
                        ref_structure,
                        min_touches=breakouts.min_touches,
                        level_tolerance_atr=breakouts.level_tolerance_atr,
                        min_break_atr=breakouts.min_break_atr,
                        min_body_ratio=breakouts.min_body_ratio,
                        volume_multiple=breakouts.volume_multiple,
                        min_room_atr=breakouts.min_room_atr,
                        max_extension_atr=breakouts.max_extension_atr,
                        break_lookback=breakouts.break_lookback,
                    )
                )

            # A breakout takes its direction from the level it broke, so it can resolve one
            # where the trend rule did not. Plan that trade too — otherwise the family's
            # defining case, a level breaking with no established trend behind it, could
            # never surface for want of levels to quote.
            decided = primary_verdict(tuple(verdicts))
            if (
                plan is None
                and has_reference
                and decided is not None
                and decided.strategy is not None
                and decided.trend is not Direction.NONE
            ):
                assert ref_candles is not None and ref_features is not None
                assert ref_structure is not None
                plan = self._planner.plan(
                    decided.trend,
                    ref_candles,
                    ref_structure,
                    ref_features.atr,
                    atr_buffer=atr_buffer,
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
                    tradingview=tv_value,
                    verdicts=tuple(verdicts),
                    reaction_patterns=reaction_patterns,
                    trendline=trendline,
                    plan=plan,
                    reason=result.reason,
                    limited_history=limited_history,
                )
            )

        # Surfacing is membership, not a threshold: a resolved direction and a usable plan.
        # With the catalogue on, the checklist decides too — a coin whose entry conditions
        # are unmet is a WAIT, and a WAIT is a watchlist entry rather than a trade.
        setups = tuple(
            a
            for a in analyses
            if a.setup_direction is not Direction.NONE and a.plan is not None
        )
        if catalogue.enabled or breakouts.enabled:
            setups = tuple(a for a in setups if a.verdict is not None and a.verdict.is_ready)
        if long_only:
            setups = tuple(a for a in setups if a.setup_direction is Direction.LONG)
        setups = tuple(sorted(setups, key=_setup_rank))
        return AnalysisRun(
            analyses=tuple(analyses),
            setups=setups,
            failures=tuple(failures),
            btc_direction=btc_direction,
        )
