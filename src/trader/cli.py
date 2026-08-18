"""Command-line interface.

This module is the only place ``typer`` and ``rich`` are imported, keeping the
analysis core interface-agnostic.
"""

from __future__ import annotations

import csv
import json
import logging
import sys
import time
from dataclasses import replace

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from trader.backtester import BTC_CONTEXT_SYMBOL, Backtester, SkippedCoin
from trader.cache_refresher import CacheRefresher, RefreshSummary
from trader.concurrent_loader import ConcurrentHistoryLoader
from trader.config import Config, ConfigError, load_config, parse_iso_date
from trader.direction import Direction
from trader.historical_data import CcxtHistoricalDataProvider, HistoricalDataProvider
from trader.indicators import compute_features
from trader.market_data import Candles, CcxtBinanceProvider, MarketDataProvider
from trader.metrics import BacktestReport, summarize
from trader.movers import MarketDataBenchmark, MomentumCoin, MoversRun, run_movers
from trader.provider import AnalysisProvider, TradingViewProvider
from trader.replay import timeframe_to_ms
from trader.runner import DEFAULT_BTC_SYMBOL, AnalysisRun, Failure, Runner, SleepFn
from trader.trade_simulator import TradeOutcome

# Default traded window for a live backtest when no range is given: the last 365 days.
_DEFAULT_BACKTEST_WINDOW_MS = 365 * 24 * 60 * 60 * 1000

app = typer.Typer(help="Advisory crypto trading bot.", add_completion=False)

# Log line format shared by the stderr and file handlers.
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# Help text reused by every command's --verbose / --log-file options.
_VERBOSE_HELP = (
    "Increase log verbosity (to stderr): -v for INFO, -vv for DEBUG. "
    "Default (no flag) logs only warnings."
)
_LOG_FILE_HELP = "Also write logs to this file (in addition to stderr)."


def configure_logging(verbosity: int, log_file: str | None = None) -> None:
    """Configure the ``"trader"`` logger for the CLI (application layer only).

    Maps ``verbosity`` 0 -> ``WARNING``, 1 -> ``INFO``, >=2 -> ``DEBUG`` on the ``"trader"``
    logger, attaches a :class:`~logging.StreamHandler` to **stderr** (so the stdout tables /
    JSON stay clean) with the shared :data:`_LOG_FORMAT`, and — when ``log_file`` is given —
    also attaches a :class:`~logging.FileHandler` to that path. Idempotent: any handlers left
    by a previous call are cleared first, so repeated invocations (``scan --watch`` or the
    test suite) never duplicate handlers. The analysis core only ever *emits* records via
    ``logging.getLogger(__name__)``; all handler/level configuration lives here.
    """

    if verbosity <= 0:
        level = logging.WARNING
    elif verbosity == 1:
        level = logging.INFO
    else:
        level = logging.DEBUG

    logger = logging.getLogger("trader")
    logger.setLevel(level)

    # Idempotent: drop any handlers a previous call attached before adding fresh ones.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(_LOG_FORMAT)

    stream_handler: logging.Handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if log_file is not None:
        file_handler: logging.Handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

# Visual style per decided direction: long green/up, short red/down, none plain.
_DIRECTION_STYLE: dict[Direction, tuple[str, str]] = {
    Direction.LONG: ("green", "▲ LONG"),
    Direction.SHORT: ("red", "▼ SHORT"),
    Direction.NONE: ("", "— NONE"),
}


def _render_failures(failures: tuple[Failure, ...], console: Console) -> None:
    if not failures:
        return

    table = Table(title="Skipped coins")
    table.add_column("Symbol")
    table.add_column("Reason")
    for failure in failures:
        table.add_row(failure.symbol, failure.reason)

    console.print(table)


def run_market_data(
    provider: MarketDataProvider,
    console: Console,
    symbol: str,
    timeframe: str,
    limit: int,
    depth: int,
) -> tuple[float, float]:
    """Fetch one coin's candles + order book and print latest close and spread.

    Returns ``(latest_close, spread)`` so the pipeline can be exercised in tests with
    a mocked provider (no network). Provider is injectable exactly like the v1 path.
    """

    candles = provider.get_ohlcv(symbol, timeframe, limit)
    order_book = provider.get_order_book(symbol, depth)
    latest_close = candles.latest_close
    spread = order_book.spread

    table = Table(title=f"Binance market data: {symbol}")
    table.add_column("Timeframe")
    table.add_column("Latest close", justify="right")
    table.add_column("Best bid", justify="right")
    table.add_column("Best ask", justify="right")
    table.add_column("Spread", justify="right")
    table.add_row(
        timeframe,
        f"{latest_close:g}",
        f"{order_book.best_bid:g}",
        f"{order_book.best_ask:g}",
        f"{spread:g}",
    )
    console.print(table)

    # Compute and display a representative subset of the technical features so the
    # indicator computation is observable end-to-end from the CLI.
    features = compute_features(candles)
    feature_table = Table(title=f"Technical features: {symbol} ({timeframe})")
    feature_table.add_column("EMA 20", justify="right")
    feature_table.add_column("EMA 50", justify="right")
    feature_table.add_column("EMA 200", justify="right")
    feature_table.add_column("RSI", justify="right")
    feature_table.add_row(
        f"{features.ema20:g}",
        f"{features.ema50:g}",
        f"{features.ema200:g}",
        f"{features.rsi:g}",
    )
    console.print(feature_table)
    return latest_close, spread


def _render_setups(
    run: AnalysisRun,
    console: Console,
    details: bool = False,
    show_all: bool = False,
) -> None:
    """Render the scored coins as a table.

    By default this is the threshold-filtered, score-ranked *short list* of surfaced
    setups (a clear direction with a total at or above the quality threshold). With
    ``show_all`` every analyzed coin is listed instead — including sub-threshold coins
    and those with no direction — so the whole picture can be seen. Alongside each
    row's direction and score the concrete trade plan (entry, stop-loss, take-profit,
    and risk-to-reward) is shown; with ``details`` the per-category breakdown behind
    the total is expanded into an extra column so the trader can see *why* it scored
    the way it did. Plans and breakdowns are read from the same in-memory analyses.
    """

    plans_by_symbol = {analysis.symbol: analysis.plan for analysis in run.analyses}
    reasons_by_symbol = {analysis.symbol: analysis.reason for analysis in run.analyses}
    limited_by_symbol = {analysis.symbol: analysis.limited_history for analysis in run.analyses}

    # The reason a coin was not surfaced is only meaningful in the full/detailed views;
    # the default short list is surfaced coins only, so it carries no reason column.
    show_reason = show_all or details

    if show_all:
        title = "All coins"
        ordered = sorted(
            run.analyses,
            key=lambda a: a.score.total if a.score is not None else -1.0,
            reverse=True,
        )
        rows = [
            (
                a.symbol,
                a.direction,
                a.score.total if a.score is not None else None,
                a.score.categories if a.score is not None else None,
            )
            for a in ordered
        ]
    else:
        title = "High-conviction setups"
        rows = [
            (score.symbol, score.direction, score.total, score.categories)
            for score in run.setups
        ]

    table = Table(title=title)
    table.add_column("Rank", justify="right")
    table.add_column("Symbol")
    table.add_column("Direction")
    table.add_column("Score/100", justify="right")
    table.add_column("Entry", justify="right")
    table.add_column("SL", justify="right")
    table.add_column("TP", justify="right")
    table.add_column("R:R", justify="right")
    if show_reason:
        table.add_column("Reason")
        table.add_column("History")
    if details:
        table.add_column("Breakdown")

    for position, (symbol, direction, total, categories) in enumerate(rows, start=1):
        style, label = _DIRECTION_STYLE[direction]
        plan = plans_by_symbol.get(symbol)
        if plan is not None:
            entry, stop_loss, take_profit, risk_reward = (
                f"{plan.entry:g}",
                f"{plan.stop_loss:g}",
                f"{plan.take_profit:g}",
                f"{plan.risk_reward:.2f}",
            )
        else:
            entry = stop_loss = take_profit = risk_reward = "—"
        cells = [
            str(position),
            symbol,
            label,
            f"{total:.1f}" if total is not None else "—",
            entry,
            stop_loss,
            take_profit,
            risk_reward,
        ]
        if show_reason:
            reason = reasons_by_symbol.get(symbol)
            cells.append(reason if reason else "—")
            cells.append("limited" if limited_by_symbol.get(symbol) else "—")
        if details:
            breakdown = (
                "  ".join(f"{c.name}={c.points:.1f}" for c in categories)
                if categories
                else "—"
            )
            cells.append(breakdown)
        table.add_row(*cells, style=style or None)

    console.print(table)


def analysis_run_to_dict(run: AnalysisRun) -> dict[str, object]:
    """Serialize an :class:`AnalysisRun` into a JSON-ready dict.

    The record is derived from the same in-memory result used to render the table, so
    the JSON output and the printed table are a single source of truth. Every analyzed
    coin is included with its direction, non-surfaced reason, limited-history marker,
    total, per-category breakdown and trade plan (whichever are available); ``setups``
    is the surfaced short list in ranked order and ``failures`` records skipped symbols.
    """

    surfaced = {score.symbol for score in run.setups}

    def plan_dict(symbol: str) -> dict[str, object] | None:
        plan = next((a.plan for a in run.analyses if a.symbol == symbol), None)
        if plan is None:
            return None
        return {
            "direction": plan.direction.value,
            "entry": plan.entry,
            "stop_loss": plan.stop_loss,
            "take_profit": plan.take_profit,
            "risk_reward": plan.risk_reward,
            "invalidation": plan.invalidation,
        }

    return {
        "coins": [
            {
                "symbol": a.symbol,
                "direction": a.direction.value,
                "surfaced": a.symbol in surfaced,
                "reason": a.reason,
                "limited_history": a.limited_history,
                "total": a.score.total if a.score is not None else None,
                "categories": [
                    {
                        "name": c.name,
                        "fraction": c.fraction,
                        "weight": c.weight,
                        "points": c.points,
                    }
                    for c in (a.score.categories if a.score is not None else ())
                ],
                "trade_plan": plan_dict(a.symbol),
            }
            for a in run.analyses
        ],
        "setups": [score.symbol for score in run.setups],
        "failures": [
            {"symbol": failure.symbol, "reason": failure.reason}
            for failure in run.failures
        ],
    }


def run_scan(
    market_data_provider: MarketDataProvider,
    analysis_provider: AnalysisProvider,
    console: Console,
    config: Config,
    sleep: SleepFn = time.sleep,
    details: bool = False,
    show_all: bool = False,
    json_path: str | None = None,
) -> AnalysisRun:
    """Run the v2 pipeline over the watchlist and render the results.

    Both providers are injectable so the pipeline can be exercised in tests with no
    network, and ``sleep`` is injectable so tests avoid wall-clock throttling delays.
    The default view is the threshold-filtered short list of surfaced setups ranked by
    score; ``show_all`` widens it to every analyzed coin and ``details`` expands the
    per-category breakdown. When ``json_path`` is given, a structured record derived
    from the same in-memory result as the table is written there. Coins whose market
    data cannot be fetched are skipped and summarized rather than aborting the run.
    """

    result = Runner(sleep=sleep).run_analysis(
        market_data=market_data_provider,
        analysis=analysis_provider,
        watchlist=config.watchlist,
        timeframes=config.timeframes,
        binance_delay=config.binance_delay,
        tradingview_delay=config.tradingview_delay,
        ohlcv_limit=config.ohlcv_lookback,
        exchange=config.exchange,
        screener=config.screener,
        category_weights=config.category_weights,
        quality_threshold=config.quality_threshold,
        relative_volume_multiple=config.relative_volume_multiple,
        min_depth=config.min_depth,
        max_spread=config.max_spread,
        atr_buffer=config.atr_buffer,
        target_rr=config.target_rr,
        reference_timeframe=config.reference_timeframe,
        long_only=config.long_only,
        lead_timeframe=config.lead_timeframe,
        htf_timeframes=tuple(config.htf_timeframes),
        require_confirmation=config.require_confirmation,
        btc_veto=config.btc_veto,
    )
    _render_setups(result, console, details=details, show_all=show_all)
    _render_failures(result.failures, console)
    if json_path is not None:
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(analysis_run_to_dict(result), handle, indent=2)
    return result


def run_scan_forever(
    market_data_provider: MarketDataProvider,
    analysis_provider: AnalysisProvider,
    console: Console,
    config: Config,
    interval: float,
    details: bool = False,
    show_all: bool = False,
    json_path: str | None = None,
    sleep: SleepFn = time.sleep,
    max_iterations: int | None = None,
) -> int:
    """Re-run the v2 pipeline on a timer, sleeping ``interval`` between runs.

    Loops forever by default; ``max_iterations`` bounds it so tests can assert the loop
    behavior without wall-clock timing. Returns the number of runs performed.
    """

    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        run_scan(
            market_data_provider,
            analysis_provider,
            console,
            config,
            sleep=sleep,
            details=details,
            show_all=show_all,
            json_path=json_path,
        )
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            break
        sleep(interval)
    return iterations


@app.command()
def scan(
    config_path: str | None = typer.Option(
        None,
        "--config",
        help="Path to a YAML configuration file. Falls back to built-in defaults.",
    ),
    details: bool = typer.Option(
        False,
        "--details",
        help="Expand the per-category breakdown behind each coin's total.",
    ),
    show_all: bool = typer.Option(
        False,
        "--all",
        help="Show all coins, including sub-threshold ones and those with no direction.",
    ),
    json_path: str | None = typer.Option(
        None,
        "--json",
        help="Write a structured JSON record of the results to this path.",
    ),
    watch: float | None = typer.Option(
        None,
        "--watch",
        help="Re-run the analysis every INTERVAL seconds until interrupted.",
    ),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help=_VERBOSE_HELP),
    log_file: str | None = typer.Option(None, "--log-file", help=_LOG_FILE_HELP),
) -> None:
    """Score each watchlist coin from live Binance + TradingView and surface setups."""

    configure_logging(verbose, log_file)
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        typer.secho(f"Configuration error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    market_data_provider = CcxtBinanceProvider()
    analysis_provider = TradingViewProvider(batch_size=config.tradingview_batch_size)
    console = Console()
    interval = watch if watch is not None else config.watch_interval
    if interval is not None:
        run_scan_forever(
            market_data_provider,
            analysis_provider,
            console,
            config,
            interval=interval,
            details=details,
            show_all=show_all,
            json_path=json_path,
        )
    else:
        run_scan(
            market_data_provider,
            analysis_provider,
            console,
            config,
            details=details,
            show_all=show_all,
            json_path=json_path,
        )


# Shown with every movers run: a scanner surfaces what is *already* moving, which is
# not a promise it keeps moving — momentum entries are often late (near tops).
_MOVERS_DISCLAIMER = (
    "Advisory only — the movers scanner surfaces coins that are already moving, not a "
    "prediction they will keep moving. Momentum entries are often late (near tops) and "
    "carry top-buying risk; this edge is unproven pending a momentum backtest."
)


def _movers_status(coin: MomentumCoin) -> str:
    """A one-word status for a coin in the ``--all`` view: surfaced / illiquid / below."""

    if coin.surfaced:
        return "surfaced"
    if not coin.liquid:
        return "illiquid"
    return "below-threshold"


def _render_movers(run: MoversRun, console: Console, show_all: bool = False) -> None:
    """Render the momentum movers as a ranked table with their trade plans.

    The default view is the threshold-clearing, liquidity-passing short list ranked by
    momentum score descending, each with its breakout/ATR trade plan (entry, stop,
    target, R:R). With ``show_all`` every evaluated coin is listed instead — including
    below-threshold and liquidity-filtered coins — with a status column. The advisory /
    top-buying-risk disclaimer is printed alongside the table.
    """

    plans_by_symbol = {coin.symbol: coin.plan for coin in run.coins}

    rows: list[tuple[str, Direction, float, str | None]]
    if show_all:
        title = "All coins"
        ordered = sorted(run.coins, key=lambda c: c.score.score, reverse=True)
        rows = [(c.symbol, c.score.direction, c.score.score, _movers_status(c)) for c in ordered]
    else:
        title = "Momentum movers"
        rows = [(s.symbol, s.direction, s.score, None) for s in run.movers]

    table = Table(title=title)
    table.add_column("Rank", justify="right")
    table.add_column("Symbol")
    table.add_column("Direction")
    table.add_column("Score/100", justify="right")
    table.add_column("Entry", justify="right")
    table.add_column("SL", justify="right")
    table.add_column("TP", justify="right")
    table.add_column("R:R", justify="right")
    if show_all:
        table.add_column("Status")

    for position, (symbol, direction, score_value, status) in enumerate(rows, start=1):
        style, label = _DIRECTION_STYLE[direction]
        plan = plans_by_symbol.get(symbol)
        if plan is not None:
            entry, stop_loss, take_profit, risk_reward = (
                f"{plan.entry:g}",
                f"{plan.stop_loss:g}",
                f"{plan.take_profit:g}",
                f"{plan.risk_reward:.2f}",
            )
        else:
            entry = stop_loss = take_profit = risk_reward = "—"
        cells = [
            str(position),
            symbol,
            label,
            f"{score_value:.1f}",
            entry,
            stop_loss,
            take_profit,
            risk_reward,
        ]
        if show_all:
            cells.append(status or "—")
        table.add_row(*cells, style=style or None)

    console.print(table)
    console.print(_MOVERS_DISCLAIMER)


def movers_run_to_dict(run: MoversRun) -> dict[str, object]:
    """Serialize a :class:`MoversRun` into a JSON-ready dict.

    Derived from the same in-memory result used to render the table, so the JSON and the
    printed table are a single source of truth. Every evaluated coin is included with its
    direction, score, factor breakdown, surfaced/liquid flags and (for surfaced coins)
    trade plan; ``movers`` is the surfaced short list in ranked order; the advisory
    disclaimer and once-per-run BTC benchmark return are included, and ``failures``
    records skipped symbols.
    """

    plans_by_symbol = {coin.symbol: coin.plan for coin in run.coins}

    def plan_dict(symbol: str) -> dict[str, object] | None:
        plan = plans_by_symbol.get(symbol)
        if plan is None:
            return None
        return {
            "direction": plan.direction.value,
            "entry": plan.entry,
            "stop_loss": plan.stop_loss,
            "take_profit": plan.take_profit,
            "risk_reward": plan.risk_reward,
            "invalidation": plan.invalidation,
        }

    return {
        "disclaimer": _MOVERS_DISCLAIMER,
        "btc_return": run.btc_return,
        "coins": [
            {
                "symbol": coin.symbol,
                "direction": coin.score.direction.value,
                "score": coin.score.score,
                "surfaced": coin.surfaced,
                "liquid": coin.liquid,
                "factors": [
                    {
                        "name": f.name,
                        "fraction": f.fraction,
                        "weight": f.weight,
                        "points": f.points,
                    }
                    for f in coin.score.factors
                ],
                "trade_plan": plan_dict(coin.symbol),
            }
            for coin in run.coins
        ],
        "movers": [score.symbol for score in run.movers],
        "failures": [
            {"symbol": failure.symbol, "reason": failure.reason}
            for failure in run.failures
        ],
    }


def run_movers_cli(
    market_data_provider: MarketDataProvider,
    console: Console,
    config: Config,
    sleep: SleepFn = time.sleep,
    *,
    show_all: bool = False,
    json_path: str | None = None,
) -> MoversRun:
    """Run the momentum scanner over the watchlist and render the ranked movers.

    The market-data provider is injectable so the pipeline can be exercised in tests
    with no network, and ``sleep`` is injectable so tests avoid wall-clock throttling.
    The BTC benchmark is derived from the same provider (fetched once per run).
    ``show_all`` widens the table to every evaluated coin; when ``json_path`` is given a
    structured record derived from the same in-memory result is written there.
    """

    benchmark = MarketDataBenchmark(market_data_provider)
    run = run_movers(
        market_data_provider,
        benchmark,
        watchlist=config.watchlist,
        momentum_weights=config.momentum_weights,
        momentum_threshold=config.momentum_threshold,
        rs_lookback_days=config.rs_lookback_days,
        breakout_lookback_days=config.breakout_lookback_days,
        min_depth=config.min_depth,
        max_spread=config.max_spread,
        long_only=config.long_only,
        atr_buffer=config.atr_buffer,
        target_rr=config.target_rr,
        ohlcv_limit=config.ohlcv_lookback,
        binance_delay=config.binance_delay,
        sleep=sleep,
    )
    _render_movers(run, console, show_all=show_all)
    _render_failures(run.failures, console)
    if json_path is not None:
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(movers_run_to_dict(run), handle, indent=2)
        console.print(f"Wrote movers report to {json_path}")
    return run


@app.command()
def movers(
    config_path: str | None = typer.Option(
        None,
        "--config",
        help="Path to a YAML configuration file. Falls back to built-in defaults.",
    ),
    show_all: bool = typer.Option(
        False,
        "--all",
        help="Show every evaluated coin, including below-threshold and illiquid ones.",
    ),
    json_path: str | None = typer.Option(
        None,
        "--json",
        help="Write the full movers report (scores + trade plans) to this JSON file.",
    ),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help=_VERBOSE_HELP),
    log_file: str | None = typer.Option(None, "--log-file", help=_LOG_FILE_HELP),
) -> None:
    """Rank watchlist coins by momentum (relative strength, breakout, volume, accel)."""

    configure_logging(verbose, log_file)
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        typer.secho(f"Configuration error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    market_data_provider = CcxtBinanceProvider()
    console = Console()
    run_movers_cli(
        market_data_provider, console, config, show_all=show_all, json_path=json_path
    )


@app.command()
def market_data(
    symbol: str = typer.Option(
        "BTC/USDT",
        "--symbol",
        help="Symbol to fetch, in ccxt unified format (e.g. BTC/USDT).",
    ),
    timeframe: str = typer.Option(
        "4h",
        "--timeframe",
        help="Candle timeframe to fetch (e.g. 15m, 1h, 4h, 1d).",
    ),
    limit: int = typer.Option(
        300,
        "--limit",
        help="Number of candles to fetch.",
    ),
    depth: int = typer.Option(
        20,
        "--depth",
        help="Order-book depth (number of levels) to fetch.",
    ),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help=_VERBOSE_HELP),
    log_file: str | None = typer.Option(None, "--log-file", help=_LOG_FILE_HELP),
) -> None:
    """Fetch one coin's candles and order book from Binance; print close and spread."""

    configure_logging(verbose, log_file)
    provider = CcxtBinanceProvider()
    console = Console()
    run_market_data(provider, console, symbol, timeframe, limit, depth)


def _bullish_frame(symbol: str, timeframe: str, step_ms: int, rows: int) -> Candles:
    """A deterministic, cleanly bullish frame on a fixed time grid for one timeframe.

    Rising sawtooth closes (so swing highs/lows step up and the structure detector
    classifies it BULLISH) over ``rows`` candles spaced ``step_ms`` apart, starting at
    ``t = 0``. Every timeframe shares the same start so the grids align across
    timeframes for point-in-time slicing.
    """

    data = []
    base = 100.0
    for i in range(rows):
        base += 1.0
        offset = {0: 0.0, 1: 3.0, 2: 5.0, 3: 3.0, 4: 0.0, 5: -2.0}[i % 6]
        close = base + offset
        data.append([i * step_ms, close - 0.5, close + 1.0, close - 1.0, close, 1_000.0 + i])
    frame = pd.DataFrame(
        data, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    return Candles(symbol=symbol, timeframe=timeframe, frame=frame)


def demo_history(
    config: Config, *, reference_bars: int = 80
) -> tuple[dict[str, dict[str, Candles]], dict[str, Candles]]:
    """Build an injected, network-free multi-timeframe history for a backtest demo.

    Until the historical-data provider lands (a later task) the ``backtest`` command
    replays over this deterministic synthetic history. Every timeframe covers the same
    wall-clock span so the grids align: the reference timeframe has ``reference_bars``
    candles and finer timeframes proportionally more. Returns ``(histories, btc)`` where
    ``histories`` maps each watchlist symbol to its per-timeframe frames and ``btc`` is
    BTC's own per-timeframe history for the point-in-time market context.
    """

    ref_step = timeframe_to_ms(config.reference_timeframe)
    span = ref_step * reference_bars

    def frames_for(symbol: str) -> dict[str, Candles]:
        out: dict[str, Candles] = {}
        for tf in config.timeframes:
            step = timeframe_to_ms(tf)
            rows = max(1, round(span / step))
            out[tf] = _bullish_frame(symbol, tf, step, rows)
        return out

    histories = {symbol: frames_for(symbol) for symbol in config.watchlist}
    btc_frames = frames_for(DEFAULT_BTC_SYMBOL)
    return histories, btc_frames


class _InMemoryHistoricalDataProvider:
    """A :class:`~trader.historical_data.HistoricalDataProvider` over in-memory frames.

    Wraps the deterministic :func:`demo_history` output so the ``backtest`` command can
    source each coin's history *through the provider seam* (exercising the provider-based
    wiring end-to-end) while staying network-free until the on-disk ccxt provider is wired
    to the CLI in a later task. Unknown ``(symbol, timeframe)`` pairs return empty candles
    so a coin without history is handled gracefully.
    """

    def __init__(
        self,
        histories: dict[str, dict[str, Candles]],
        btc_frames: dict[str, Candles],
        btc_symbol: str,
    ) -> None:
        self._frames: dict[str, dict[str, Candles]] = {**histories, btc_symbol: btc_frames}

    def get_history(self, symbol: str, timeframe: str, start: int, end: int) -> Candles:
        frames = self._frames.get(symbol)
        if frames is None or timeframe not in frames:
            empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
            return Candles(symbol=symbol, timeframe=timeframe, frame=empty)
        return frames[timeframe]


def _fmt_pf(profit_factor: float) -> str:
    return "inf" if profit_factor == float("inf") else f"{profit_factor:.2f}"


def _render_backtest_report(
    report: BacktestReport,
    skipped: tuple[SkippedCoin, ...],
    console: Console,
) -> None:
    table = Table(title="Backtest results")
    table.add_column("Resolved trades", justify="right")
    table.add_column("Wins", justify="right")
    table.add_column("Losses", justify="right")
    table.add_column("Win rate", justify="right")
    table.add_column("Unresolved", justify="right")
    win_rate = f"{report.win_rate * 100:.1f}%" if report.resolved_trades else "n/a"
    table.add_row(
        str(report.resolved_trades),
        str(report.wins),
        str(report.losses),
        win_rate,
        str(report.unresolved_trades),
    )
    console.print(table)

    metrics = Table(title="Performance metrics")
    metrics.add_column("Expectancy (R)", justify="right")
    metrics.add_column("Profit factor", justify="right")
    metrics.add_column("Avg win (R)", justify="right")
    metrics.add_column("Avg loss (R)", justify="right")
    metrics.add_column("Largest win (R)", justify="right")
    metrics.add_column("Largest loss (R)", justify="right")
    metrics.add_column("Max drawdown", justify="right")
    metrics.add_row(
        f"{report.expectancy:.3f}",
        _fmt_pf(report.profit_factor),
        f"{report.avg_win:.3f}",
        f"{report.avg_loss:.3f}",
        f"{report.largest_win:.3f}",
        f"{report.largest_loss:.3f}",
        f"{report.max_drawdown * 100:.1f}%",
    )
    console.print(metrics)

    for title, breakdowns in (
        ("Per-coin breakdown", report.per_coin),
        ("Per-direction breakdown", report.per_direction),
    ):
        if not breakdowns:
            continue
        bt = Table(title=title)
        bt.add_column("Key")
        bt.add_column("Resolved", justify="right")
        bt.add_column("Wins", justify="right")
        bt.add_column("Losses", justify="right")
        bt.add_column("Win rate", justify="right")
        bt.add_column("Expectancy (R)", justify="right")
        for b in breakdowns:
            bt.add_row(
                b.key,
                str(b.resolved_trades),
                str(b.wins),
                str(b.losses),
                f"{b.win_rate * 100:.1f}%",
                f"{b.expectancy:.3f}",
            )
        console.print(bt)

    if skipped:
        skip_table = Table(title="Skipped coins")
        skip_table.add_column("Symbol")
        skip_table.add_column("Reason")
        for coin in skipped:
            skip_table.add_row(coin.symbol, coin.reason)
        console.print(skip_table)

    console.print(
        "Past performance is not indicative of future results. "
        "Historical liquidity/spread is not modeled (neutral full-credit liquidity)."
    )


def _trade_rows(outcomes: tuple[TradeOutcome, ...]) -> list[dict[str, object]]:
    """One JSON/CSV-ready record per simulated trade — the full auditable log."""

    return [
        {
            "symbol": o.symbol,
            "direction": o.direction.value,
            "entry_time": o.entry_time,
            "entry_price": o.entry_price,
            "exit_time": o.exit_time,
            "exit_price": o.exit_price,
            "result": o.result.value,
            "r_multiple": o.r_multiple,
            "costs": o.costs,
        }
        for o in outcomes
    ]


def backtest_report_to_dict(
    report: BacktestReport, outcomes: tuple[TradeOutcome, ...]
) -> dict[str, object]:
    """Serialize the summary plus the full per-trade log into a JSON-ready dict.

    ``profit_factor`` of infinity (wins but no losing R) is emitted as ``null`` so the
    output is valid JSON. The trade log is derived from the same outcomes summarized into
    ``report``, so the export and the printed tables are a single source of truth.
    """

    pf = report.profit_factor
    return {
        "summary": {
            "resolved_trades": report.resolved_trades,
            "wins": report.wins,
            "losses": report.losses,
            "win_rate": report.win_rate,
            "unresolved_trades": report.unresolved_trades,
            "expectancy": report.expectancy,
            "profit_factor": None if pf == float("inf") else pf,
            "avg_win": report.avg_win,
            "avg_loss": report.avg_loss,
            "largest_win": report.largest_win,
            "largest_loss": report.largest_loss,
            "max_drawdown": report.max_drawdown,
            "risk_per_trade": report.risk_per_trade,
            "equity_curve": list(report.equity_curve),
            "per_coin": [
                {
                    "key": b.key,
                    "resolved_trades": b.resolved_trades,
                    "wins": b.wins,
                    "losses": b.losses,
                    "win_rate": b.win_rate,
                    "expectancy": b.expectancy,
                }
                for b in report.per_coin
            ],
            "per_direction": [
                {
                    "key": b.key,
                    "resolved_trades": b.resolved_trades,
                    "wins": b.wins,
                    "losses": b.losses,
                    "win_rate": b.win_rate,
                    "expectancy": b.expectancy,
                }
                for b in report.per_direction
            ],
        },
        "trades": _trade_rows(outcomes),
    }


_TRADE_LOG_COLUMNS = [
    "symbol",
    "direction",
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "result",
    "r_multiple",
    "costs",
]


def write_trade_log_csv(path: str, outcomes: tuple[TradeOutcome, ...]) -> None:
    """Write the full per-trade log to ``path`` as CSV: one row per simulated trade."""

    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_TRADE_LOG_COLUMNS)
        writer.writeheader()
        for row in _trade_rows(outcomes):
            writer.writerow(row)


def _finish_backtest(
    report: BacktestReport,
    outcomes: tuple[TradeOutcome, ...],
    skipped: tuple[SkippedCoin, ...],
    console: Console,
    json_path: str | None,
    csv_path: str | None,
) -> None:
    _render_backtest_report(report, skipped, console)
    if json_path is not None:
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(backtest_report_to_dict(report, outcomes), handle, indent=2)
    if csv_path is not None:
        write_trade_log_csv(csv_path, outcomes)


def run_backtest(
    histories: dict[str, dict[str, Candles]],
    btc_frames: dict[str, Candles],
    config: Config,
    console: Console,
    json_path: str | None = None,
    csv_path: str | None = None,
) -> BacktestReport:
    """Replay the live pipeline over injected history and render the full report.

    ``histories`` and ``btc_frames`` are injected so the whole pipe can be exercised in
    tests with no network. Renders the summary, performance metrics, and per-coin /
    per-direction breakdowns; optionally exports the summary + full per-trade log as JSON
    and/or the per-trade log as CSV. Returns the :class:`~trader.metrics.BacktestReport`.
    """

    run = Backtester().run(config, histories, btc_frames)
    report = summarize(run.outcomes)
    _finish_backtest(report, run.outcomes, run.skipped, console, json_path, csv_path)
    return report


@app.command()
def backtest(
    config_path: str | None = typer.Option(
        None,
        "--config",
        help="Path to a YAML configuration file. Falls back to built-in defaults.",
    ),
    from_date: str | None = typer.Option(
        None,
        "--from",
        help="Start of the traded date range (ISO date, e.g. 2024-01-01). Overrides config.",
    ),
    to_date: str | None = typer.Option(
        None,
        "--to",
        help="End of the traded date range (ISO date, e.g. 2024-06-30). Overrides config.",
    ),
    json_path: str | None = typer.Option(
        None,
        "--json",
        help="Write the summary plus the full per-trade log to this path as JSON.",
    ),
    csv_path: str | None = typer.Option(
        None,
        "--csv",
        help="Write the full per-trade log to this path as CSV (one row per trade).",
    ),
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Replay over a deterministic in-memory demo history instead of fetching "
        "real candles (network-free; for a quick smoke test).",
    ),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help=_VERBOSE_HELP),
    log_file: str | None = typer.Option(None, "--log-file", help=_LOG_FILE_HELP),
) -> None:
    """Replay the scoring engine over historical candles and report the measured edge.

    Reuses the live scan configuration (watchlist, profile, weights, threshold, direction
    rule) and the ``backtest`` config section (fees, slippage, risk-per-trade, holding cap,
    date range, cache directory). ``--from`` / ``--to`` override the configured range; only
    trades entered within it are counted, with earlier candles used solely to warm up the
    indicators. By default, real candles are fetched from Binance (paginated and cached on
    disk under the configured cache directory); ``--demo`` replays a network-free synthetic
    history instead. ``--json`` / ``--csv`` export the summary and full per-trade log.
    """

    configure_logging(verbose, log_file)
    try:
        config = load_config(config_path)
        bt = config.backtest
        start_iso = from_date if from_date is not None else bt.start
        end_iso = to_date if to_date is not None else bt.end
        entry_start = parse_iso_date(start_iso, "--from") if start_iso is not None else None
        entry_end = parse_iso_date(end_iso, "--to") if end_iso is not None else None
    except ConfigError as exc:
        typer.secho(f"Configuration error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    if entry_start is not None and entry_end is not None and entry_start > entry_end:
        typer.secho(
            "Configuration error: --from must not be after --to",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    console = Console()
    backtester = Backtester(
        fee_rate=bt.fee_rate,
        slippage=bt.slippage,
        max_holding_bars=bt.max_holding_bars,
    )

    provider: HistoricalDataProvider
    if demo:
        # Deterministic, network-free synthetic history on a t=0 grid; the gate range
        # defaults to the whole demo span so every demo bar is sourced.
        histories, btc_frames = demo_history(config)
        provider = _InMemoryHistoricalDataProvider(histories, btc_frames, BTC_CONTEXT_SYMBOL)
        demo_end = max(
            int(candles.frame["timestamp"].iloc[-1])
            for frames in (*histories.values(), btc_frames)
            for candles in frames.values()
        )
        start = entry_start if entry_start is not None else 0
        end = entry_end if entry_end is not None else demo_end
    else:
        # Real candles: paginated + cached under the configured cache directory. The
        # traded window defaults to the last year up to now when not given (indicator
        # warm-up bars are fetched before `start` by the provider).
        provider = CcxtHistoricalDataProvider(cache_dir=bt.cache_dir)
        end = entry_end if entry_end is not None else int(time.time() * 1000)
        start = entry_start if entry_start is not None else end - _DEFAULT_BACKTEST_WINDOW_MS

    run = backtester.run_from_provider(config, provider, start=start, end=end)
    report = summarize(run.outcomes, risk_per_trade=bt.risk_per_trade)
    _finish_backtest(report, run.outcomes, run.skipped, console, json_path, csv_path)


def _render_refresh_summary(summary: RefreshSummary, console: Console) -> None:
    """Print the cache-refresh outcome: coins refreshed, candles added, and any skips."""

    table = Table(title="Cache refresh")
    table.add_column("Coins refreshed", justify="right")
    table.add_column("Candles added", justify="right")
    table.add_column("Coins skipped", justify="right")
    table.add_row(
        str(summary.coins_refreshed),
        str(summary.candles_added),
        str(len(summary.skipped)),
    )
    console.print(table)

    if summary.skipped:
        skip_table = Table(title="Skipped coins")
        skip_table.add_column("Symbol")
        skip_table.add_column("Reason")
        for coin in summary.skipped:
            skip_table.add_row(coin.symbol, coin.reason)
        console.print(skip_table)


@app.command()
def refresh_cache(
    config_path: str | None = typer.Option(
        None,
        "--config",
        help="Path to a YAML configuration file. Falls back to built-in defaults.",
    ),
    history_days: int | None = typer.Option(
        None,
        "--history-days",
        help="Override the cold-start history horizon (days) for this run. Bounds how far "
        "back a cold cache is filled; ignored once the cache is warm.",
    ),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help=_VERBOSE_HELP),
    log_file: str | None = typer.Option(None, "--log-file", help=_LOG_FILE_HELP),
) -> None:
    """Top up the on-disk candle cache for the watchlist so later runs read warm data.

    Brings the cache up to the latest completed candle for every configured coin (plus the
    BTC context) and timeframe, fetching only what is newer than the cache (nothing on a
    fully-warm cache) and never storing the still-forming candle. On a cold cache the fill
    goes back no further than the configured history horizon (override with
    ``--history-days``). Prints a summary of coins refreshed, candles added, and any skips.

    This command has no built-in scheduler: run it once a day on a schedule you manage. On
    Linux/macOS use cron (e.g. a crontab line ``0 6 * * * trader refresh-cache --config
    config.yaml``); on macOS you can instead use launchd with a StartCalendarInterval
    ``launchd`` agent. After the daily refresh, interactive ``backtest``/``scan`` runs read
    the warm cache and only ever fetch the small still-forming part of the current period.
    """

    configure_logging(verbose, log_file)
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        typer.secho(f"Configuration error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    if history_days is not None:
        if history_days <= 0:
            typer.secho(
                "Configuration error: --history-days must be positive",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        config = replace(
            config,
            backtest=replace(config.backtest, history_horizon_days=history_days),
        )

    provider = CcxtHistoricalDataProvider(cache_dir=config.backtest.cache_dir)
    loader = ConcurrentHistoryLoader()
    console = Console()
    summary = CacheRefresher().refresh(config, provider, loader)
    _render_refresh_summary(summary, console)


if __name__ == "__main__":
    app()
