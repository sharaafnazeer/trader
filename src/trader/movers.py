"""Orchestration for the momentum / breakout scanner ("movers").

:func:`run_movers` walks a watchlist through the live market-data seam and ranks the
coins by their :class:`~trader.momentum.MomentumScore`. It is the momentum counterpart
to :meth:`trader.runner.Runner.run_analysis` and is deliberately kept on a **separate
path**: it does not touch the trend ``scan`` pipeline, the backtester, or their
scoring / direction / trade-plan engines.

The BTC benchmark return is fetched **once per run** (BTC daily candles over the
relative-strength lookback) and every coin is scored relative to it. Per coin, live
daily candles plus one order-book snapshot are fetched; a coin failing the liquidity
filter (``min_depth`` / ``max_spread``) is excluded from the surfaced movers but
**retained** in the returned :class:`MoversRun` (so a later ``--all`` view is purely
additive), as are coins scoring below the momentum threshold. A coin whose market data
cannot be fetched is recorded as a :class:`~trader.runner.Failure` and skipped so one
bad symbol never aborts the run.

Both data seams (the market-data provider and the BTC benchmark) are injectable so the
orchestration is fully testable with no network. This module belongs to the analysis
core; it does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from trader.config import (
    DEFAULT_ATR_BUFFER,
    DEFAULT_BREAKOUT_LOOKBACK_DAYS,
    DEFAULT_LONG_ONLY,
    DEFAULT_MAX_SPREAD,
    DEFAULT_MIN_DEPTH,
    DEFAULT_MOMENTUM_THRESHOLD,
    DEFAULT_MOMENTUM_WEIGHTS,
    DEFAULT_RS_LOOKBACK_DAYS,
    DEFAULT_TARGET_RR,
)
from trader.direction import Direction
from trader.indicators import compute_features
from trader.market_data import (
    DEFAULT_OHLCV_LIMIT,
    DEFAULT_ORDER_BOOK_DEPTH,
    Candles,
    MarketDataProvider,
    OrderBook,
)
from trader.momentum import (
    MomentumModel,
    MomentumScore,
    passes_liquidity,
    period_return,
    trailing_breakout_level,
)
from trader.runner import DEFAULT_BINANCE_DELAY, DEFAULT_BTC_SYMBOL, Failure
from trader.structure import Structure, StructureState
from trader.trade_planner import TradePlan, TradePlanner

logger = logging.getLogger(__name__)

# The single timeframe the momentum scanner reads: live daily candles.
DEFAULT_MOVERS_TIMEFRAME = "1d"

SleepFn = Callable[[float], None]


class BenchmarkProvider(Protocol):
    """Supplies the once-per-run BTC benchmark return the movers are scored against."""

    def btc_return(self, lookback_days: int, ohlcv_limit: int) -> float: ...


class MarketDataBenchmark:
    """A :class:`BenchmarkProvider` backed by the live market-data seam.

    Fetches BTC's daily candles through the same :class:`MarketDataProvider` the coins
    use and returns its simple return over the lookback. Kept as its own seam so tests
    can inject a fixed benchmark, while the default wiring reuses the Binance provider.
    """

    def __init__(
        self,
        market_data: MarketDataProvider,
        btc_symbol: str = DEFAULT_BTC_SYMBOL,
        timeframe: str = DEFAULT_MOVERS_TIMEFRAME,
    ) -> None:
        self._market_data = market_data
        self._btc_symbol = btc_symbol
        self._timeframe = timeframe

    def btc_return(self, lookback_days: int, ohlcv_limit: int) -> float:
        candles = self._market_data.get_ohlcv(self._btc_symbol, self._timeframe, ohlcv_limit)
        return period_return(candles, lookback_days)


# A momentum plan has no swing structure to draw targets from — it anchors on the
# breakout level instead — so it passes empty pivots and the take-profit falls back to
# the ``target_rr`` floor.
_EMPTY_STRUCTURE = StructureState(structure=Structure.BROKEN, swing_highs=(), swing_lows=())


@dataclass(frozen=True, eq=False)
class MomentumCoin:
    """A single evaluated coin: its momentum score, order book, plan, and gating flags.

    ``liquid`` is whether the order book cleared the liquidity filter; ``surfaced`` is
    whether the coin is liquid, at/above the momentum threshold, and (under ``long_only``)
    not a suppressed short. Coins that are illiquid, below threshold, or long-only-
    suppressed are retained here (not surfaced) so a later ``--all`` view is additive.
    ``plan`` is the breakout/ATR trade plan for a surfaced coin (``None`` otherwise).
    ``eq=False`` because the wrapped :class:`Candles` frame is not scalar-comparable and
    identity is sufficient here.
    """

    symbol: str
    score: MomentumScore
    order_book: OrderBook
    candles: Candles
    liquid: bool
    surfaced: bool
    plan: TradePlan | None = None


def plan_for_mover(
    candles: Candles,
    score: MomentumScore,
    *,
    breakout_lookback: int = DEFAULT_BREAKOUT_LOOKBACK_DAYS,
    atr_buffer: float = DEFAULT_ATR_BUFFER,
    target_rr: float = DEFAULT_TARGET_RR,
) -> TradePlan | None:
    """Build a breakout/ATR trade plan for a surfaced mover by reusing :class:`TradePlanner`.

    The trailing breakout level (the prior ``breakout_lookback`` candles' high for a long,
    low for a short) is injected as the plan's invalidation, so the stop sits an
    ``atr_buffer × ATR`` beyond it — below the trailing high for a long, above the trailing
    low for a short — and the target is floored by ``target_rr``. Returns ``None`` when the
    geometry is degenerate (a non-positive risk), so the caller degrades gracefully.
    """

    features = compute_features(candles)
    level = trailing_breakout_level(candles, breakout_lookback, score.direction)
    return TradePlanner().plan(
        score.direction,
        candles,
        _EMPTY_STRUCTURE,
        features.atr,
        atr_buffer=atr_buffer,
        target_rr=target_rr,
        invalidation=level,
    )


@dataclass(frozen=True)
class MoversRun:
    """The outcome of a single momentum scan.

    ``coins`` holds every successfully-fetched coin (surfaced, below-threshold, and
    liquidity-filtered alike) so nothing is lost; ``movers`` is the surfaced short list
    ranked by score descending; ``btc_return`` is the once-per-run benchmark the coins
    were scored against; ``failures`` records skipped symbols.
    """

    coins: tuple[MomentumCoin, ...]
    movers: tuple[MomentumScore, ...]
    btc_return: float
    failures: tuple[Failure, ...] = ()


def run_movers(
    market_data: MarketDataProvider,
    benchmark: BenchmarkProvider,
    watchlist: list[str],
    *,
    timeframe: str = DEFAULT_MOVERS_TIMEFRAME,
    momentum_weights: dict[str, float] | None = None,
    momentum_threshold: float = DEFAULT_MOMENTUM_THRESHOLD,
    rs_lookback_days: int = DEFAULT_RS_LOOKBACK_DAYS,
    breakout_lookback_days: int = DEFAULT_BREAKOUT_LOOKBACK_DAYS,
    min_depth: float = DEFAULT_MIN_DEPTH,
    max_spread: float = DEFAULT_MAX_SPREAD,
    long_only: bool = DEFAULT_LONG_ONLY,
    atr_buffer: float = DEFAULT_ATR_BUFFER,
    target_rr: float = DEFAULT_TARGET_RR,
    ohlcv_limit: int = DEFAULT_OHLCV_LIMIT,
    order_book_depth: int = DEFAULT_ORDER_BOOK_DEPTH,
    binance_delay: float = DEFAULT_BINANCE_DELAY,
    sleep: SleepFn = time.sleep,
) -> MoversRun:
    """Rank the watchlist by momentum against a once-per-run BTC benchmark.

    The BTC benchmark return is fetched a single time up front (regardless of watchlist
    size) and injected into every coin's score. For each coin: fetch live daily candles
    plus one order-book snapshot, apply the liquidity filter, and score it via
    :class:`~trader.momentum.MomentumModel`. A coin at/above ``momentum_threshold`` that
    also clears the liquidity filter is *surfaced* and gets a breakout/ATR
    :class:`~trader.trade_planner.TradePlan`; illiquid, below-threshold, and (under
    ``long_only``) short coins are retained in the result but not surfaced and carry no
    plan. Binance requests are throttled by the injected ``sleep`` (skipping the sleep
    before the very first request); a coin whose market data cannot be fetched is recorded
    as a :class:`~trader.runner.Failure` and skipped. The surfaced ``movers`` are ranked by
    score descending.
    """

    weights = momentum_weights if momentum_weights is not None else dict(DEFAULT_MOMENTUM_WEIGHTS)
    model = MomentumModel()

    # BTC benchmark return: fetched exactly once per run, whatever the watchlist size.
    btc_return = benchmark.btc_return(rs_lookback_days, ohlcv_limit)
    logger.info(
        "scanning %d coins for momentum (BTC benchmark return %.4f)",
        len(watchlist),
        btc_return,
    )

    # Throttle: sleep the delay before every Binance request except the very first.
    first_request = True

    def wait() -> None:
        nonlocal first_request
        if first_request:
            first_request = False
        else:
            sleep(binance_delay)

    coins: list[MomentumCoin] = []
    failures: list[Failure] = []
    for symbol in watchlist:
        logger.info("evaluating %s", symbol)
        try:
            wait()
            candles = market_data.get_ohlcv(symbol, timeframe, ohlcv_limit)
            wait()
            order_book = market_data.get_order_book(symbol, order_book_depth)
        except Exception as exc:  # noqa: BLE001 - any market-data error degrades gracefully
            reason = str(exc) or repr(exc)
            logger.warning("skipping %s: %s", symbol, reason)
            failures.append(Failure(symbol=symbol, reason=reason))
            continue

        score = model.score(
            symbol,
            candles,
            btc_return,
            weights,
            rs_lookback=rs_lookback_days,
            breakout_lookback=breakout_lookback_days,
        )
        liquid = passes_liquidity(order_book, min_depth, max_spread)
        long_only_suppressed = long_only and score.direction is Direction.SHORT
        surfaced = liquid and score.score >= momentum_threshold and not long_only_suppressed
        plan = (
            plan_for_mover(
                candles,
                score,
                breakout_lookback=breakout_lookback_days,
                atr_buffer=atr_buffer,
                target_rr=target_rr,
            )
            if surfaced
            else None
        )
        coins.append(
            MomentumCoin(
                symbol=symbol,
                score=score,
                order_book=order_book,
                candles=candles,
                liquid=liquid,
                surfaced=surfaced,
                plan=plan,
            )
        )

    movers = tuple(
        sorted(
            (coin.score for coin in coins if coin.surfaced),
            key=lambda score: score.score,
            reverse=True,
        )
    )
    return MoversRun(
        coins=tuple(coins),
        movers=movers,
        btc_return=btc_return,
        failures=tuple(failures),
    )
