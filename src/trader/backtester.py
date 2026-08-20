"""Backtest orchestration: replay the live pipeline over point-in-time history.

:class:`Backtester` drives one coin end-to-end. It marches through the reference
timeframe's close timestamps and, at each one, slices *every* timeframe to the bars
that had closed by then (via :class:`~trader.replay.Replay`) and runs the **live**
analysis pipeline on those truncated frames — the exact same calls the live scanner
makes per coin:

    ``indicators.compute_features`` -> ``structure.analyze`` -> ``direction.decide``
    -> ``trade_planner.TradePlanner().plan``

so there is no re-implementation of the strategy and no parallel indicator path. Two
correctness stances from the spec are encoded here:

* **BTC market context is point-in-time.** BTC's regime is decided from BTC's *own*
  ``Replay``-sliced frames at each moment, never from the whole/live frame, so a later
  BTC move cannot leak into an earlier evaluation.
* **No liquidity model.** No historical order book exists. The 0-100 quality score that
  once consumed one was retired with the indicators behind it, so the backtest no longer
  needs a synthetic stand-in; historical liquidity and spread are simply not modelled (a
  tradeability gate, not a directional predictor).

Lifecycle: one open trade per coin at a time, entered only on a *fresh transition*
into a surfaced setup — the rising edge of qualification. While a signal persists no
second trade opens, and after a trade closes a still-qualifying signal does not
immediately re-enter until the signal has reset and re-qualified.

This module belongs to the analysis core: it is pure and deterministic, performs no
I/O, and does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass

from trader.breakout import evaluate_breakout
from trader.concurrent_loader import ConcurrentHistoryLoader, SkippedCoin
from trader.config import Config
from trader.direction import Direction, MarketContext, decide
from trader.historical_data import HistoricalDataProvider
from trader.indicators import compute_features
from trader.market_data import Candles
from trader.patterns import detect as detect_patterns
from trader.replay import Replay, timeframe_to_ms
from trader.runner import primary_verdict
from trader.strategy import SetupVerdict
from trader.strategy import evaluate as evaluate_strategy
from trader.structure import analyze as analyze_structure
from trader.trade_planner import TradePlan, TradePlanner
from trader.trade_simulator import EntrySetup, TradeOutcome, TradeSimulator
from trader.trendline import fit as fit_trendline

logger = logging.getLogger(__name__)

# The BTC symbol whose own point-in-time frames decide the market context. Kept here
# (rather than importing the runner, which is not part of the pure backtest core) so the
# provider-sourced backtest can fetch BTC's history alongside the watchlist coins.
BTC_CONTEXT_SYMBOL = "BTC/USDT"



@dataclass(frozen=True)
class Evaluation:
    """The live pipeline's per-moment output for one coin on sliced frames.

    ``surfaced`` is ``True`` when the coin resolved a direction, produced a trade plan, and
    — when the catalogue is enabled — its strategy checklist reads READY. ``verdict`` is
    that checklist result, ``None`` when the catalogue is off.
    """

    direction: Direction
    plan: TradePlan | None
    surfaced: bool
    verdict: SetupVerdict | None = None


@dataclass(frozen=True)
class BacktestRun:
    """The collected outcomes of a backtest across the watchlist.

    ``skipped`` lists coins that could not be backtested (e.g. no history for the range);
    the run always completes for the remaining coins.
    """

    outcomes: tuple[TradeOutcome, ...]
    skipped: tuple[SkippedCoin, ...] = ()


# Minimum bars a sliced frame needs before the indicator stack is run. Below this the
# longer-window indicators (ATR/MACD/Bollinger) are undefined or raise, so an early
# moment with too little warm-up history is simply not evaluated (it can never surface).
_MIN_WARMUP_BARS = 30


def _sufficient(frames: dict[str, Candles]) -> bool:
    """True when every sliced frame has enough bars to run the indicator pipeline."""
    return all(len(candles.frame) >= _MIN_WARMUP_BARS for candles in frames.values())


def market_context_at(
    btc_frames: dict[str, Candles], ts: int, config: Config
) -> MarketContext:
    """BTC's market context decided from its own frames sliced at ``ts``.

    Mirrors the live pipeline's BTC regime decision (``compute_features`` ->
    ``analyze`` -> ``decide`` with an empty context), but on the ``Replay``-sliced
    frames only, so a BTC move after ``ts`` cannot change the returned context.
    Degrades to a neutral context (no regime) when BTC has no bars yet at ``ts``.
    """

    if not btc_frames:
        return MarketContext()
    sliced = Replay(
        btc_frames, config.reference_timeframe, max_bars=config.ohlcv_lookback
    ).slice_at(ts)
    if not _sufficient(sliced):
        return MarketContext()

    features_by_tf = {tf: compute_features(c) for tf, c in sliced.items()}
    structure_by_tf = {tf: analyze_structure(c) for tf, c in sliced.items()}
    btc_direction = decide(
        features_by_tf,
        structure_by_tf,
        MarketContext(),
        htf_timeframes=tuple(config.htf_timeframes),
        lead_timeframe=config.lead_timeframe,
        require_confirmation=config.require_confirmation,
        btc_veto=config.btc_veto,
    ).direction
    return MarketContext(btc_direction=btc_direction)


class MarketContexts:
    """BTC's point-in-time regime, computed once per timestamp and shared across coins.

    BTC's regime at a moment does not depend on which coin is being evaluated, but the
    per-coin replay recomputed it for every one of them — measured at **half the run's
    CPU** on an 86-coin watchlist, spent deriving the same answer 86 times.

    This is memoisation, not an approximation: :func:`market_context_at` is pure, so the
    cached value is the value that would have been computed. The cache is per-run and holds
    one small record per reference close, which is nothing beside the frames themselves.
    """

    def __init__(self, btc_frames: dict[str, Candles], config: Config) -> None:
        self._btc_frames = btc_frames
        self._config = config
        self._cache: dict[int, MarketContext] = {}

    def at(self, ts: int) -> MarketContext:
        """BTC's context at ``ts``, computing it only the first time it is asked for."""

        cached = self._cache.get(ts)
        if cached is None:
            cached = market_context_at(self._btc_frames, ts, self._config)
            self._cache[ts] = cached
        return cached


def _evaluate(
    symbol: str,
    sliced: dict[str, Candles],
    btc_context: MarketContext,
    config: Config,
    planner: TradePlanner,
) -> Evaluation:
    """Run the live pipeline on already-sliced frames — the reused per-coin path."""

    if not _sufficient(sliced):
        return Evaluation(Direction.NONE, None, surfaced=False)

    features_by_tf = {tf: compute_features(c) for tf, c in sliced.items()}
    structure_by_tf = {tf: analyze_structure(c) for tf, c in sliced.items()}
    result = decide(
        features_by_tf,
        structure_by_tf,
        btc_context,
        htf_timeframes=tuple(config.htf_timeframes),
        lead_timeframe=config.lead_timeframe,
        require_confirmation=config.require_confirmation,
        btc_veto=config.btc_veto,
    )
    direction = result.direction
    if direction is Direction.NONE:
        return Evaluation(Direction.NONE, None, surfaced=False)

    ref_candles = sliced.get(config.reference_timeframe)
    ref_structure = structure_by_tf.get(config.reference_timeframe)
    ref_features = features_by_tf.get(config.reference_timeframe)
    plan: TradePlan | None = None
    if ref_candles is not None and ref_structure is not None and ref_features is not None:
        plan = planner.plan(
            direction,
            ref_candles,
            ref_structure,
            ref_features.atr,
            atr_buffer=config.atr_buffer,
            target_rr=config.target_rr,
        )

    # The checklist decides here exactly as it does live, through the same evaluator on the
    # same reference-timeframe inputs. Wiring only the live path would leave the backtest
    # measuring "every direction change with a plan" — a different strategy from the one the
    # scan runs, and a number that describes nothing the trader would have traded.
    verdicts: list[SetupVerdict] = []
    if config.strategies.enabled and ref_features is not None and ref_structure is not None:
        assert ref_candles is not None
        catalogue = config.strategies
        pivots = (
            list(zip(ref_structure.swing_low_indices, ref_structure.swing_lows, strict=False))
            if direction is Direction.LONG
            else list(
                zip(ref_structure.swing_high_indices, ref_structure.swing_highs, strict=False)
            )
        )
        verdicts.append(evaluate_strategy(
            symbol,
            direction,
            ref_features,
            ref_structure,
            ref_candles.latest_close,
            patterns=detect_patterns(
                ref_candles,
                bars=catalogue.reaction_lookback,
                wick_body_ratio=catalogue.pattern_wick_body_ratio,
            ),
            trendline=fit_trendline(
                pivots,
                len(ref_candles.frame) - 1,
                min_touches=catalogue.trendline_min_touches,
                min_r_squared=catalogue.trendline_min_r2,
            ),
            trendline_tolerance_atr=catalogue.trendline_tolerance_atr,
            max_extension_atr=catalogue.max_extension_atr,
            stoch_oversold=catalogue.stoch_oversold,
            stoch_overbought=catalogue.stoch_overbought,
            cross_lookback=catalogue.cross_lookback,
        ))

    # Same reasoning as the checklist above: wiring the breakout family only into the live
    # path would leave it permanently unmeasured, which is the state its default exists to
    # advertise. It takes its direction from the level, so it runs regardless of ``direction``.
    if config.breakout.enabled and ref_features is not None and ref_structure is not None:
        assert ref_candles is not None
        breakouts = config.breakout
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

    verdict = primary_verdict(tuple(verdicts))
    # A breakout can resolve a direction the trend rule did not, and the trade taken is the
    # one the deciding verdict describes — so the traded direction comes from it when it
    # has one, exactly as the live path's ``setup_direction`` does.
    traded = (
        verdict.trend
        if verdict is not None and verdict.strategy is not None
        and verdict.trend is not Direction.NONE
        else direction
    )
    surfaced = (
        plan is not None
        and (not config.long_only or traded is Direction.LONG)
        and (verdict is None or verdict.is_ready)
    )
    return Evaluation(traded, plan, surfaced=surfaced, verdict=verdict)


def evaluate_at(
    frames: dict[str, Candles],
    ts: int,
    btc_context: MarketContext,
    config: Config,
) -> Evaluation:
    """Slice ``frames`` at ``ts`` and run the live pipeline — the no-look-ahead unit.

    Convenience wrapper that constructs a :class:`~trader.replay.Replay`, slices, and
    evaluates. Because the slice drops every bar closing after ``ts``, a future spike
    appended to ``frames`` cannot change the returned :class:`Evaluation`.
    """

    symbol = frames[config.reference_timeframe].symbol
    sliced = Replay(
        frames, config.reference_timeframe, max_bars=config.ohlcv_lookback
    ).slice_at(ts)
    return _evaluate(symbol, sliced, btc_context, config, TradePlanner())


def _resolve_workers(configured: int, coins: int) -> int:
    """How many worker processes to replay with.

    ``0`` means auto: leave two cores for the parent and the machine, and never spawn more
    workers than there are coins. Capped because the win flattens once every core is busy
    and each worker holds a copy of one coin's frames.
    """

    if configured > 0:
        return max(1, min(configured, coins))
    available = os.cpu_count() or 1
    return max(1, min(available - 2, coins, 16))


def _finer_bars_after(frames: dict[str, Candles], finer_tf: str, ts: int) -> Candles:
    """The finer-timeframe bars opening at or after ``ts`` (the bars after entry)."""

    candles = frames[finer_tf]
    frame = candles.frame
    kept = frame[frame["timestamp"] >= ts].reset_index(drop=True)
    return Candles(symbol=candles.symbol, timeframe=finer_tf, frame=kept)


@dataclass(frozen=True)
class _CoinEvaluation:
    """One coin's decision points and what was decided, as plain picklable data.

    Deliberately not a :class:`_CoinTimeline`: that carries a closure over the coin's frames
    and the simulator, neither of which crosses a process boundary. Splitting the phases here
    is what lets the expensive half — the point-in-time replay — run in a worker while the
    lifecycle stays in the parent, where it must be, because entries depend on what else is
    open.
    """

    symbol: str
    ref_closes: tuple[int, ...]
    surfaced: tuple[bool, ...]
    plans: tuple[TradePlan | None, ...]


def _evaluate_coin(
    symbol: str,
    frames: dict[str, Candles],
    contexts: dict[int, MarketContext],
    config: Config,
    entry_start: int | None,
    entry_end: int | None,
) -> _CoinEvaluation:
    """Replay one coin point-in-time. Pure, top-level, and picklable — the worker body.

    ``contexts`` is the BTC regime per reference close, computed once in the parent and
    passed in: recomputing it per worker would reintroduce the duplication that memoising it
    removed, and it is small enough to send.
    """

    replay = Replay(frames, config.reference_timeframe, max_bars=config.ohlcv_lookback)
    ref_closes = replay.reference_closes()
    planner = TradePlanner()

    evaluations = [
        _evaluate(
            symbol,
            replay.slice_at(ts),
            contexts.get(ts, MarketContext()),
            config,
            planner,
        )
        for ts in ref_closes
    ]
    surfaced = tuple(
        e.surfaced
        and (entry_start is None or ts >= entry_start)
        and (entry_end is None or ts <= entry_end)
        for e, ts in zip(evaluations, ref_closes, strict=True)
    )
    return _CoinEvaluation(
        symbol=symbol,
        ref_closes=tuple(ref_closes),
        surfaced=surfaced,
        plans=tuple(e.plan for e in evaluations),
    )


@dataclass(frozen=True, eq=False)
class _CoinTimeline:
    """One coin's decision points, whether each qualifies, and how to enter at one.

    Produced once per coin by the evaluation phase and consumed by whichever lifecycle
    runs — per-coin or portfolio-wide. Deliberately lean: the per-moment
    per-moment evaluation is dropped after ``surfaced`` is derived from it, because the
    portfolio lifecycle holds every coin's timeline at once and retaining the full
    evaluations for an 87-coin, ~9,855-close run would cost well over a gigabyte where
    this costs tens of megabytes.

    ``enter`` returns ``None`` when the setup carried a pending entry zone that price never
    traded into within the fill window — no position was ever opened, which is why that is
    not an unresolved trade.

    ``eq=False`` because it carries a closure and is identified by identity.
    """

    symbol: str
    ref_closes: tuple[int, ...]
    surfaced: tuple[bool, ...]
    enter: Callable[[int], TradeOutcome | None]
    index_by_ts: dict[int, int]


def _run_portfolio_lifecycle(timelines: Sequence[_CoinTimeline]) -> list[TradeOutcome]:
    """Apply the per-coin lifecycle across a merged, chronological timeline.

    Identical in behaviour to :func:`_run_lifecycle` run once per coin — the same rising
    edge, the same flat check, the same entry — but walking time-major instead of
    coin-major, so that a portfolio-level view of what is open at each moment becomes
    possible. Each coin keeps its own previous-surfaced flag and its own
    become-flat timestamp, exactly as before; they simply live in dictionaries keyed by
    coin rather than in local variables inside a per-coin loop.

    The timeline is the **union** of every coin's reference closes, because coins with
    different amounts of history do not share a grid. Within one timestamp, coins are
    visited in the order given, which makes the walk deterministic.

    The only observable difference from the per-coin version is the order outcomes are
    appended: time-major here, coin-major there. Reports are unaffected — the equity
    curve sorts by entry time before computing.
    """

    timeline = sorted({ts for t in timelines for ts in t.ref_closes})
    prev: dict[str, bool] = {t.symbol: False for t in timelines}
    open_until: dict[str, float | None] = {t.symbol: None for t in timelines}

    outcomes: list[TradeOutcome] = []
    for ts in timeline:
        for coin in timelines:
            index = coin.index_by_ts.get(ts)
            if index is None:
                continue
            surfaced = coin.surfaced[index]
            until = open_until[coin.symbol]
            flat = until is None or ts >= until
            if surfaced and not prev[coin.symbol] and flat:
                outcome = coin.enter(index)
                # A pending zone that was never touched opens nothing: the coin stays flat
                # and no outcome is recorded, rather than an unresolved trade being invented.
                if outcome is not None:
                    outcomes.append(outcome)
                    open_until[coin.symbol] = (
                        outcome.exit_time if outcome.exit_time is not None else float("inf")
                    )
            prev[coin.symbol] = surfaced
    return outcomes


def _run_lifecycle(
    ref_closes: tuple[int, ...],
    surfaced: list[bool],
    enter: Callable[[int], TradeOutcome | None],
) -> list[TradeOutcome]:
    """Apply the one-position / enter-on-transition lifecycle over the closes.

    A trade is entered only on the rising edge of ``surfaced`` (a fresh transition into
    qualification) and only while flat (no trade still open at that moment). ``enter``
    is called with the index of the entering close and returns the resolved
    :class:`~trader.trade_simulator.TradeOutcome`; its ``exit_time`` marks when the coin
    becomes flat again. An unresolved trade (no exit) keeps the coin occupied for the
    rest of the run. Because the edge only fires once per surfaced run, a persistent
    signal never re-enters, and a still-qualifying signal after a close does not
    re-enter until it has reset and re-qualified. ``enter`` returning ``None`` (a pending
    entry zone price never reached) leaves the coin flat and records nothing.
    """

    outcomes: list[TradeOutcome] = []
    prev = False
    open_until: float | None = None
    for i, ts in enumerate(ref_closes):
        s = surfaced[i]
        flat = open_until is None or ts >= open_until
        if s and not prev and flat:
            outcome = enter(i)
            if outcome is not None:
                outcomes.append(outcome)
                open_until = (
                    outcome.exit_time if outcome.exit_time is not None else float("inf")
                )
        prev = s
    return outcomes


# In-code backtest cost defaults — results are net by default (set both to zero for the
# raw gross edge). Modest values in the spirit of a liquid-pair taker fill; task 05 lifts
# them, plus the optional holding cap, onto the validated YAML config surface.
DEFAULT_BACKTEST_FEE_RATE = 0.0004
DEFAULT_BACKTEST_SLIPPAGE = 0.0005
DEFAULT_MAX_HOLDING_BARS: int | None = None


class Backtester:
    """Replays the live pipeline over injected history and simulates each setup.

    ``fee_rate``/``slippage`` are applied to every simulated entry and exit so results
    are net of costs by default; passing zero for both yields the gross edge.
    ``max_holding_bars`` optionally time-stops a trade at market. These are in-code
    defaults for now (task 05 lifts them onto the YAML config surface).
    """

    def __init__(
        self,
        *,
        fee_rate: float = DEFAULT_BACKTEST_FEE_RATE,
        slippage: float = DEFAULT_BACKTEST_SLIPPAGE,
        max_holding_bars: int | None = DEFAULT_MAX_HOLDING_BARS,
    ) -> None:
        self._planner = TradePlanner()
        self._simulator = TradeSimulator()
        self._fee_rate = fee_rate
        self._slippage = slippage
        self._max_holding_bars = max_holding_bars

    def _timeline(
        self,
        evaluation: _CoinEvaluation,
        frames: dict[str, Candles],
        config: Config,
    ) -> _CoinTimeline:
        """Attach the entry closure to an evaluated coin — the half that stays in-process.

        The closure captures the coin's frames and this instance's simulator, so it cannot
        cross a process boundary; it does not need to, because simulating an entry is cheap
        beside the replay that produced the decision points.
        """

        finer_tf = config.timeframes[0]
        ref_closes = evaluation.ref_closes
        plans = evaluation.plans
        symbol = evaluation.symbol

        def enter(i: int) -> TradeOutcome | None:
            ts = ref_closes[i]
            plan = plans[i]
            assert plan is not None  # surfaced implies a plan
            setup = EntrySetup(
                symbol=symbol,
                direction=plan.direction,
                entry_time=ts,
                entry=plan.entry,
                stop=plan.stop_loss,
                target=plan.take_profit,
            )
            return self._simulator.simulate(
                setup,
                _finer_bars_after(frames, finer_tf, ts),
                fee_rate=self._fee_rate,
                slippage=self._slippage,
                max_holding_bars=self._max_holding_bars,
            )

        return _CoinTimeline(
            symbol=symbol,
            ref_closes=ref_closes,
            surfaced=evaluation.surfaced,
            enter=enter,
            index_by_ts={ts: i for i, ts in enumerate(ref_closes)},
        )

    def _prepare_coin(
        self,
        symbol: str,
        frames: dict[str, Candles],
        config: Config,
        contexts: MarketContexts,
        *,
        entry_start: int | None = None,
        entry_end: int | None = None,
    ) -> _CoinTimeline:
        """Evaluate every decision point for one coin, ready for any lifecycle to consume.

        This is the whole evaluation phase and it is deliberately shared: the per-coin and
        the portfolio lifecycles both run against the output of this one method, so they
        cannot diverge on *what was evaluated* — only on the order in which entries are
        taken. That is what makes their equivalence structural rather than coincidental.

        When ``entry_start``/``entry_end`` (ms) are given, a trade may only be *entered*
        while its reference close falls within ``[entry_start, entry_end]``; earlier bars
        still feed the sliced frames (so the indicators are warm) but never open a trade.
        """

        replay = Replay(frames, config.reference_timeframe, max_bars=config.ohlcv_lookback)
        resolved = {ts: contexts.at(ts) for ts in replay.reference_closes()}
        evaluation = _evaluate_coin(
            symbol, frames, resolved, config, entry_start, entry_end
        )
        return self._timeline(evaluation, frames, config)

    def run_coin(
        self,
        symbol: str,
        frames: dict[str, Candles],
        btc_frames: dict[str, Candles],
        config: Config,
        *,
        contexts: MarketContexts | None = None,
        entry_start: int | None = None,
        entry_end: int | None = None,
    ) -> tuple[TradeOutcome, ...]:
        """Backtest a single coin in isolation; returns its trade outcomes.

        ``contexts`` lets a caller looping over coins share one BTC-regime cache; without
        it each call builds its own, which is correct but recomputes BTC's regime per coin.

        Retained as the reference implementation the portfolio replay is proven equivalent
        to. Kept rather than deleted precisely so that proof has something to compare
        against.
        """

        prepared = self._prepare_coin(
            symbol, frames, config,
            contexts if contexts is not None else MarketContexts(btc_frames, config),
            entry_start=entry_start, entry_end=entry_end,
        )
        return tuple(
            _run_lifecycle(prepared.ref_closes, list(prepared.surfaced), prepared.enter)
        )

    def run(
        self,
        config: Config,
        histories: dict[str, dict[str, Candles]],
        btc_frames: dict[str, Candles],
        *,
        entry_start: int | None = None,
        entry_end: int | None = None,
    ) -> BacktestRun:
        """Backtest every watchlist coin that has injected history; collect outcomes.

        Every coin is evaluated first, then a **single chronological walk** over the merged
        timeline applies the entry lifecycle. That ordering is what allows portfolio-level
        rules — such as a cap on concurrent positions — to be expressed at all: walking one
        coin start-to-finish, as :meth:`run_per_coin` does, leaves nothing that knows what
        else is open at the moment a trade would open.

        With no portfolio rule in force the two produce the same trades and identical
        reports; only the order outcomes are collected differs.

        ``histories`` maps a symbol to its full per-timeframe frames; ``btc_frames`` is
        BTC's own per-timeframe history used to decide the point-in-time market context.
        A coin without frames is simply skipped. ``entry_start``/``entry_end`` (ms)
        optionally gate entries to a date range (earlier bars warm up the indicators).
        """

        # One cache for the whole run: BTC's regime at a moment is the same for every coin,
        # and recomputing it per coin was half the run's CPU.
        contexts = MarketContexts(btc_frames, config)

        timelines: list[_CoinTimeline] = []
        for symbol in config.watchlist:
            frames = histories.get(symbol)
            if frames is None:
                continue
            timelines.append(
                self._prepare_coin(
                    symbol, frames, config, contexts,
                    entry_start=entry_start, entry_end=entry_end,
                )
            )
            logger.info("evaluated %s", symbol)

        outcomes = _run_portfolio_lifecycle(timelines)
        logger.info(
            "backtest complete: %d trades across %d coins", len(outcomes), len(timelines)
        )
        return BacktestRun(outcomes=tuple(outcomes))

    def run_per_coin(
        self,
        config: Config,
        histories: dict[str, dict[str, Candles]],
        btc_frames: dict[str, Candles],
        *,
        entry_start: int | None = None,
        entry_end: int | None = None,
    ) -> BacktestRun:
        """The original coin-at-a-time replay, retained as the equivalence reference.

        :meth:`run` walks a merged chronological timeline so that portfolio-level rules can
        be applied; this walks each coin start to finish in isolation, as the engine did
        before. The two must produce the same set of trades and identical summarized
        reports — that is asserted by test, and it is what licenses trusting any later
        measurement made through :meth:`run`.
        """

        contexts = MarketContexts(btc_frames, config)

        outcomes: list[TradeOutcome] = []
        for symbol in config.watchlist:
            frames = histories.get(symbol)
            if frames is None:
                continue
            outcomes.extend(
                self.run_coin(
                    symbol, frames, btc_frames, config,
                    contexts=contexts, entry_start=entry_start, entry_end=entry_end,
                )
            )
        return BacktestRun(outcomes=tuple(outcomes))

    def run_from_provider(
        self,
        config: Config,
        provider: HistoricalDataProvider,
        *,
        start: int,
        end: int,
    ) -> BacktestRun:
        """Source each coin's per-timeframe history from ``provider`` and backtest it.

        All watchlist coins **and** the BTC context are pre-fetched *concurrently* through a
        :class:`~trader.concurrent_loader.ConcurrentHistoryLoader` (bounded by
        ``config.backtest.max_workers``), then each coin is replayed sequentially — the
        replay is pure and fast, so only the network fetch is parallelised and the numbers
        are unchanged. A coin whose frames come back empty for *any* timeframe, or whose
        fetch failed after retries, is recorded in :attr:`BacktestRun.skipped`; the run
        completes for the rest without aborting. BTC's own frames decide the point-in-time
        market context; if BTC has no history (or its fetch failed) the context degrades to
        neutral rather than being reported as a skipped watchlist coin.
        """

        loader = ConcurrentHistoryLoader()
        symbols = [*config.watchlist, BTC_CONTEXT_SYMBOL]
        histories, skipped_coins = loader.load(
            provider,
            symbols,
            config.timeframes,
            start,
            end,
            max_workers=config.backtest.max_workers,
        )

        # BTC's history for the market context; degrades to neutral (an empty mapping) when
        # BTC has no history or its fetch failed.
        btc_frames = histories.get(BTC_CONTEXT_SYMBOL) or {}
        skip_reasons = {coin.symbol: coin.reason for coin in skipped_coins}

        # One BTC-regime cache for the whole run; see :class:`MarketContexts`.
        contexts = MarketContexts(btc_frames, config)

        skipped: list[SkippedCoin] = []
        present: list[str] = []
        for symbol in config.watchlist:
            if symbol in histories:
                present.append(symbol)
                continue
            # The loader already classified why (fetch failure vs no data); fall back to
            # the no-data reason if the symbol somehow produced neither result.
            reason = skip_reasons.get(symbol, "no historical data available for range")
            logger.warning("skipping %s in backtest: %s", symbol, reason)
            skipped.append(SkippedCoin(symbol=symbol, reason=reason))

        evaluations = self._evaluate_all(
            present, histories, contexts, config, start, end
        )

        outcomes: list[TradeOutcome] = []
        for symbol in present:
            timeline = self._timeline(evaluations[symbol], histories[symbol], config)
            coin_outcomes = _run_lifecycle(
                timeline.ref_closes, list(timeline.surfaced), timeline.enter
            )
            logger.info("backtested %s: %d trades", symbol, len(coin_outcomes))
            outcomes.extend(coin_outcomes)

        logger.info(
            "backtest complete: %d trades across %d coins, %d skipped",
            len(outcomes),
            len(present),
            len(skipped),
        )
        return BacktestRun(outcomes=tuple(outcomes), skipped=tuple(skipped))

    def _evaluate_all(
        self,
        symbols: Sequence[str],
        histories: dict[str, dict[str, Candles]],
        contexts: MarketContexts,
        config: Config,
        start: int | None,
        end: int | None,
    ) -> dict[str, _CoinEvaluation]:
        """Replay every coin, across processes when there is more than one worker.

        Coins are independent — nothing in a coin's point-in-time replay reads another
        coin — so the expensive half parallelises exactly. The lifecycle stays sequential in
        the parent, because *that* is where coins interact: what a coin may enter depends on
        what else is already open.

        The BTC contexts are resolved here, once, and the per-coin subset is sent with each
        task. Letting each worker compute its own would undo the memoisation that made the
        sequential run twice as fast.
        """

        workers = _resolve_workers(config.backtest.replay_workers, len(symbols))
        tasks = []
        for symbol in symbols:
            replay = Replay(
                histories[symbol], config.reference_timeframe, max_bars=config.ohlcv_lookback
            )
            resolved = {ts: contexts.at(ts) for ts in replay.reference_closes()}
            tasks.append((symbol, histories[symbol], resolved, config, start, end))

        if workers <= 1:
            return {t[0]: _evaluate_coin(*t) for t in tasks}

        logger.info("replaying %d coins across %d workers", len(tasks), workers)
        results: dict[str, _CoinEvaluation] = {}
        # An explicit *spawn* context rather than the platform default. By the time this
        # runs, ``run_from_provider`` has already fetched history through the loader's
        # thread pool, and forking a process that has had threads is a known deadlock: the
        # child inherits the memory image, including locks held at fork time by threads
        # that do not exist in it. macOS already defaults to spawn, so this changes nothing
        # here; Linux defaults to fork, which is where it would eventually bite. Spawn
        # re-imports this module per worker, which costs nothing beside the replay itself.
        with ProcessPoolExecutor(
            max_workers=workers, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            futures = {pool.submit(_evaluate_coin, *t): t[0] for t in tasks}
            for future in as_completed(futures):
                evaluation = future.result()
                results[evaluation.symbol] = evaluation
        return results


# Re-exported for the timeframe duration helper's convenience (kept importable here so
# the CLI's synthetic-history builder can align timeframes without a second import).
__all__ = [
    "BTC_CONTEXT_SYMBOL",
    "Backtester",
    "BacktestRun",
    "Evaluation",
    "SkippedCoin",
    "evaluate_at",
    "market_context_at",
    "timeframe_to_ms",
]
