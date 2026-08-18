"""Backtest orchestration: replay the live pipeline over point-in-time history.

:class:`Backtester` drives one coin end-to-end. It marches through the reference
timeframe's close timestamps and, at each one, slices *every* timeframe to the bars
that had closed by then (via :class:`~trader.replay.Replay`) and runs the **live**
analysis pipeline on those truncated frames — the exact same calls the live scanner
makes per coin:

    ``indicators.compute_features`` -> ``structure.analyze`` -> ``direction.decide``
    -> ``scoring_model.ScoringModel().score`` -> ``trade_planner.TradePlanner().plan``

so there is no re-implementation of the strategy and no parallel indicator path. Two
correctness stances from the spec are encoded here:

* **BTC market context is point-in-time.** BTC's regime is decided from BTC's *own*
  ``Replay``-sliced frames at each moment, never from the whole/live frame, so a later
  BTC move cannot leak into an earlier evaluation.
* **Neutral full-credit liquidity.** No historical order book exists, so the scoring
  model is handed a synthetic order book that yields a liquidity fraction of ``1.0``.
  The 0-100 scale and the quality threshold are unchanged, so a backtest score equals
  the live pipeline's score for the same sliced inputs. Historical liquidity/spread is
  therefore not modeled (a tradeability gate, not a directional predictor).

Lifecycle: one open trade per coin at a time, entered only on a *fresh transition*
into a surfaced setup — the rising edge of qualification. While a signal persists no
second trade opens, and after a trade closes a still-qualifying signal does not
immediately re-enter until the signal has reset and re-qualified.

This module belongs to the analysis core: it is pure and deterministic, performs no
I/O, and does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from trader.concurrent_loader import ConcurrentHistoryLoader, SkippedCoin
from trader.config import Config
from trader.direction import Direction, MarketContext, decide
from trader.historical_data import HistoricalDataProvider
from trader.indicators import compute_features
from trader.market_data import Candles, OrderBook
from trader.replay import Replay, timeframe_to_ms
from trader.scoring_model import QualityScore, ScoringModel
from trader.structure import analyze as analyze_structure
from trader.trade_planner import TradePlan, TradePlanner
from trader.trade_simulator import EntrySetup, TradeOutcome, TradeSimulator

logger = logging.getLogger(__name__)

# The BTC symbol whose own point-in-time frames decide the market context. Kept here
# (rather than importing the runner, which is not part of the pure backtest core) so the
# provider-sourced backtest can fetch BTC's history alongside the watchlist coins.
BTC_CONTEXT_SYMBOL = "BTC/USDT"

# A synthetic order book that yields a liquidity fraction of exactly 1.0 regardless of
# the configured depth/spread gates: unbounded depth on both sides and a zero spread
# (best bid == best ask). This is how the backtest supplies neutral, full-credit
# liquidity in the absence of any historical order book, so the score matches live.
NEUTRAL_LIQUIDITY_FRACTION = 1.0
_NEUTRAL_PRICE = 1.0
_NEUTRAL_DEPTH = 1e18


def neutral_order_book(symbol: str) -> OrderBook:
    """A full-credit order book for ``symbol`` (fraction 1.0) — see module docstring."""

    return OrderBook(
        symbol=symbol,
        best_bid=_NEUTRAL_PRICE,
        best_ask=_NEUTRAL_PRICE,
        bid_depth=_NEUTRAL_DEPTH,
        ask_depth=_NEUTRAL_DEPTH,
    )


@dataclass(frozen=True)
class Evaluation:
    """The live pipeline's per-moment output for one coin on sliced frames.

    ``surfaced`` is ``True`` only when the coin resolved a direction, cleared the
    quality threshold, and produced a trade plan — i.e. it is enterable.
    """

    direction: Direction
    score: QualityScore | None
    plan: TradePlan | None
    surfaced: bool


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


def _evaluate(
    symbol: str,
    sliced: dict[str, Candles],
    btc_context: MarketContext,
    config: Config,
    model: ScoringModel,
    planner: TradePlanner,
) -> Evaluation:
    """Run the live pipeline on already-sliced frames — the reused per-coin path."""

    if not _sufficient(sliced):
        return Evaluation(Direction.NONE, None, None, surfaced=False)

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
        return Evaluation(Direction.NONE, None, None, surfaced=False)

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

    close_by_tf = {tf: c.latest_close for tf, c in sliced.items()}
    # No historical order book: neutral full-credit liquidity so the score matches the
    # live pipeline's for the same sliced inputs. TradingView is absent in a backtest.
    score = model.score(
        symbol=symbol,
        direction=direction,
        features_by_tf=features_by_tf,
        structure_by_tf=structure_by_tf,
        btc_context=btc_context,
        weights=config.category_weights,
        tradingview=None,
        order_book=neutral_order_book(symbol),
        close_by_tf=close_by_tf,
        relative_volume_multiple=config.relative_volume_multiple,
        min_depth=config.min_depth,
        max_spread=config.max_spread,
        risk_reward=plan.risk_reward if plan is not None else None,
        target_rr=config.target_rr,
    )

    surfaced = (
        plan is not None
        and score.total >= config.quality_threshold
        and (not config.long_only or direction is Direction.LONG)
    )
    return Evaluation(direction, score, plan, surfaced=surfaced)


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
    return _evaluate(symbol, sliced, btc_context, config, ScoringModel(), TradePlanner())


def _finer_bars_after(frames: dict[str, Candles], finer_tf: str, ts: int) -> Candles:
    """The finer-timeframe bars opening at or after ``ts`` (the bars after entry)."""

    candles = frames[finer_tf]
    frame = candles.frame
    kept = frame[frame["timestamp"] >= ts].reset_index(drop=True)
    return Candles(symbol=candles.symbol, timeframe=finer_tf, frame=kept)


def _run_lifecycle(
    ref_closes: tuple[int, ...],
    surfaced: list[bool],
    enter: Callable[[int], TradeOutcome],
) -> list[TradeOutcome]:
    """Apply the one-position / enter-on-transition lifecycle over the closes.

    A trade is entered only on the rising edge of ``surfaced`` (a fresh transition into
    qualification) and only while flat (no trade still open at that moment). ``enter``
    is called with the index of the entering close and returns the resolved
    :class:`~trader.trade_simulator.TradeOutcome`; its ``exit_time`` marks when the coin
    becomes flat again. An unresolved trade (no exit) keeps the coin occupied for the
    rest of the run. Because the edge only fires once per surfaced run, a persistent
    signal never re-enters, and a still-qualifying signal after a close does not
    re-enter until it has reset and re-qualified.
    """

    outcomes: list[TradeOutcome] = []
    prev = False
    open_until: float | None = None
    for i, ts in enumerate(ref_closes):
        s = surfaced[i]
        flat = open_until is None or ts >= open_until
        if s and not prev and flat:
            outcome = enter(i)
            outcomes.append(outcome)
            open_until = outcome.exit_time if outcome.exit_time is not None else float("inf")
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
        self._model = ScoringModel()
        self._planner = TradePlanner()
        self._simulator = TradeSimulator()
        self._fee_rate = fee_rate
        self._slippage = slippage
        self._max_holding_bars = max_holding_bars

    def run_coin(
        self,
        symbol: str,
        frames: dict[str, Candles],
        btc_frames: dict[str, Candles],
        config: Config,
        *,
        entry_start: int | None = None,
        entry_end: int | None = None,
    ) -> tuple[TradeOutcome, ...]:
        """Backtest a single coin; returns its resolved/unresolved trade outcomes.

        When ``entry_start``/``entry_end`` (ms) are given, a trade may only be *entered*
        while its reference close falls within ``[entry_start, entry_end]``; earlier bars
        still feed the sliced frames (so the indicators are warm) but never open a trade,
        so only trades entered within the requested range are counted.
        """

        finer_tf = config.timeframes[0]
        # Bound the sliced feature window to the live-scan lookback: the indicators only
        # need ~200 bars, so keeping the last ``ohlcv_lookback`` (default 300) turns the
        # per-close slicing from O(N^2) into O(N) while matching the live scan window.
        # ``reference_closes()`` still walks the full reference history (it is unbounded).
        replay = Replay(
            frames, config.reference_timeframe, max_bars=config.ohlcv_lookback
        )
        ref_closes = replay.reference_closes()

        # Evaluate every moment first (point-in-time), then apply the lifecycle.
        evaluations: list[Evaluation] = []
        for ts in ref_closes:
            btc_context = market_context_at(btc_frames, ts, config)
            sliced = replay.slice_at(ts)
            evaluations.append(
                _evaluate(symbol, sliced, btc_context, config, self._model, self._planner)
            )
        # Gate entries to the requested range: a close outside it can never open a trade,
        # but its slice was still used above to warm the indicators.
        surfaced = [
            e.surfaced
            and (entry_start is None or ts >= entry_start)
            and (entry_end is None or ts <= entry_end)
            for e, ts in zip(evaluations, ref_closes, strict=True)
        ]

        def enter(i: int) -> TradeOutcome:
            ts = ref_closes[i]
            plan = evaluations[i].plan
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

        return tuple(_run_lifecycle(ref_closes, surfaced, enter))

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

        ``histories`` maps a symbol to its full per-timeframe frames; ``btc_frames`` is
        BTC's own per-timeframe history used to decide the point-in-time market context.
        A coin without frames is simply skipped. ``entry_start``/``entry_end`` (ms)
        optionally gate entries to a date range (earlier bars warm up the indicators).
        """

        outcomes: list[TradeOutcome] = []
        for symbol in config.watchlist:
            frames = histories.get(symbol)
            if frames is None:
                continue
            coin_outcomes = self.run_coin(
                symbol,
                frames,
                btc_frames,
                config,
                entry_start=entry_start,
                entry_end=entry_end,
            )
            logger.info("backtested %s: %d trades", symbol, len(coin_outcomes))
            outcomes.extend(coin_outcomes)
        logger.info("backtest complete: %d trades", len(outcomes))
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

        outcomes: list[TradeOutcome] = []
        skipped: list[SkippedCoin] = []
        for symbol in config.watchlist:
            frames = histories.get(symbol)
            if frames is None:
                # The loader already classified why (fetch failure vs no data); fall back to
                # the no-data reason if the symbol somehow produced neither result.
                reason = skip_reasons.get(symbol, "no historical data available for range")
                logger.warning("skipping %s in backtest: %s", symbol, reason)
                skipped.append(SkippedCoin(symbol=symbol, reason=reason))
                continue
            coin_outcomes = self.run_coin(
                symbol, frames, btc_frames, config, entry_start=start, entry_end=end
            )
            logger.info("backtested %s: %d trades", symbol, len(coin_outcomes))
            outcomes.extend(coin_outcomes)

        logger.info(
            "backtest complete: %d trades across %d coins, %d skipped",
            len(outcomes),
            len(config.watchlist) - len(skipped),
            len(skipped),
        )
        return BacktestRun(outcomes=tuple(outcomes), skipped=tuple(skipped))


# Re-exported for the timeframe duration helper's convenience (kept importable here so
# the CLI's synthetic-history builder can align timeframes without a second import).
__all__ = [
    "BTC_CONTEXT_SYMBOL",
    "Backtester",
    "BacktestRun",
    "Evaluation",
    "NEUTRAL_LIQUIDITY_FRACTION",
    "SkippedCoin",
    "evaluate_at",
    "market_context_at",
    "neutral_order_book",
    "timeframe_to_ms",
]
