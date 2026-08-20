"""Command-line interface.

This module is the only place ``typer`` and ``rich`` are imported, keeping the
analysis core interface-agnostic.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import replace

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from trader.analyst import (
    Action,
    Analyst,
    AnalystError,
    AnalystReview,
    AnalystVerdict,
    RejectedVerdict,
    partition_verdicts,
)
from trader.backtester import BTC_CONTEXT_SYMBOL, Backtester, SkippedCoin
from trader.brief import (
    MarketBrief,
    SetupBrief,
    build_brief,
    build_mover_brief,
    render_briefs,
)
from trader.cache_refresher import CacheRefresher, RefreshSummary
from trader.candidate_gate import (
    CooldownState,
    pair_candidates,
    pair_movers,
    record_selection,
    select,
)
from trader.concurrent_loader import ConcurrentHistoryLoader
from trader.config import (
    DEFAULT_ENV_FILE,
    Config,
    ConfigError,
    load_config,
    load_env_file,
    parse_iso_date,
    resolve_ai_credential,
    resolve_telegram_credentials,
)
from trader.decision_log import (
    SCANNER_MOVERS,
    SCANNER_SCAN,
    DecisionLog,
    DecisionRecord,
    records_for_failure,
    records_for_review,
)
from trader.direction import Direction
from trader.historical_data import CcxtHistoricalDataProvider, HistoricalDataProvider
from trader.indicators import compute_features
from trader.market_data import Candles, CcxtBinanceProvider, MarketDataProvider
from trader.metrics import BacktestReport, MetricDelta, compare_summaries, summarize
from trader.momentum import trailing_breakout_level
from trader.movers import (
    DEFAULT_MOVERS_TIMEFRAME,
    MarketDataBenchmark,
    MomentumCoin,
    MoversRun,
    run_movers,
)
from trader.notify import (
    Notifier,
    NotifyError,
    NullNotifier,
    TelegramNotifier,
    format_alert,
    should_alert,
)
from trader.openai_analyst import OpenAIAnalyst, openai_completion_fn
from trader.provider import AnalysisProvider, TradingViewProvider
from trader.replay import timeframe_to_ms
from trader.runner import DEFAULT_BTC_SYMBOL, AnalysisRun, CoinAnalysis, Failure, Runner, SleepFn
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


# Rich falls back to 80 columns when stdout is not a terminal — which is exactly when the
# output is being piped to a file, a log or a pager. Eighty is too narrow for the setups
# table, and rich's response to a narrow column is to *ellipsize*: a stop-loss of
# 0.00267387 prints as "0.00267…". A trade level that silently loses digits is worse than
# no output at all, so a redirected run is given a width the table fits in. On a real
# terminal the width is left alone, so it still adapts to the window.
NON_TERMINAL_WIDTH = 140

def add_numeric_column(table: Table, header: str) -> None:
    """Add a numeric column that wraps rather than ellipsizes when space runs short.

    Rich's default overflow is ``ellipsis``, which turns a stop-loss of 0.00267387 into
    "0.00267…" in a narrow terminal. Folding costs a second line instead of the tail of the
    number, which is the right trade for anything the trader acts on: a clipped *symbol* is
    an annoyance, a clipped *level* is a wrong order.
    """

    table.add_column(header, justify="right", overflow="fold", no_wrap=False)


def make_console() -> Console:
    """The CLI's console: auto-width on a real terminal, a fitting width when redirected.

    The test is ``isatty``, not rich's ``is_terminal``. Those disagree exactly where it
    matters: with ``FORCE_COLOR`` set (common in CI and in some shells) rich reports a
    terminal for a piped stdout and *still* falls back to 80 columns — so branching on
    ``is_terminal`` would leave the redirected case broken in the environments most likely
    to hit it. An explicit ``COLUMNS`` is honoured either way, because someone who set it
    means it.
    """

    if sys.stdout.isatty() or os.environ.get("COLUMNS"):
        return Console()
    return Console(width=NON_TERMINAL_WIDTH)


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
    feature_table.add_column("EMA 10", justify="right")
    feature_table.add_column("EMA 21", justify="right")
    feature_table.add_column("EMA 50", justify="right")
    feature_table.add_column("SMA 200", justify="right")
    feature_table.add_row(
        f"{features.ema10:g}",
        f"{features.ema21:g}",
        f"{features.ema50:g}",
        f"{features.sma200:g}",
    )
    console.print(feature_table)
    return latest_close, spread


def _setups_cell(analysis: CoinAnalysis) -> str:
    """Every setup that matched this coin, not only the one deciding its row.

    A coin can present a trend pullback and a breakout at once. Showing just the deciding
    verdict would hide the second, and the second is often the more interesting fact — two
    independent methods pointing at the same coin is not the same evidence as one.
    """

    matched = sorted(
        v.strategy.value for v in analysis.verdicts if v.strategy is not None
    )
    return "+".join(matched) if matched else "—"


def _render_setups(
    run: AnalysisRun,
    console: Console,
    details: bool = False,
    show_all: bool = False,
) -> None:
    """Render the scan as the method's four-part answer per coin.

    Trend, setup, entry status and decision are separate columns because they are separate
    facts: a coin can have a textbook trend and no entry, and collapsing that into one
    verdict is the misread the whole catalogue exists to prevent. WAIT is expected to be the
    common answer, so a scan with nothing ready says so in words rather than printing an
    empty table and leaving the trader to wonder whether it ran.

    By default only READY setups are listed. ``show_all`` lists every analysed coin —
    including the waiting ones and those matching no setup — with the reason attached.
    """

    if show_all:
        title = "All coins"
        # Closest to ready first, so the coins worth watching are at the top and the ones
        # matching no setup fall to the bottom. ``run.setups`` is already ranked.
        rows = sorted(
            run.analyses,
            key=lambda a: (
                -(a.verdict.conditions_met if a.verdict is not None else 0),
                a.symbol,
            ),
        )
    else:
        title = "Setups — ready to trade"
        rows = list(run.setups)

    plans = {a.symbol: a.plan for a in run.analyses}

    table = Table(title=title)
    table.add_column("#", justify="right")
    table.add_column("Symbol")
    table.add_column("Trend")
    table.add_column("Setup")
    table.add_column("Entry")
    table.add_column("Decision")
    # The two views answer different questions and so carry different columns. The default
    # view is a trade list and needs the levels. ``--all`` is diagnostic — *why is this coin
    # not ready?* — where levels for a coin that is not a trade crowd out the reason, which
    # is the only thing on the row worth reading.
    if not show_all:
        add_numeric_column(table, "Entry@")
        add_numeric_column(table, "SL")
        add_numeric_column(table, "TP")
        add_numeric_column(table, "R:R")
        table.add_column("Target", max_width=12, overflow="ellipsis")
    if show_all or details:
        # Reason is prose, so it clips rather than wrapping: over 86 coins the full text
        # turns every row into a paragraph and the table stops being scannable. A minimum
        # width keeps it useful — squeezed to ten characters it says nothing at all. The
        # full text is never lost: it goes to the analyst's evidence verbatim.
        table.add_column(
            "Reason", min_width=40, max_width=70, no_wrap=True, overflow="ellipsis"
        )
        table.add_column("History")

    for position, analysis in enumerate(rows, start=1):
        verdict = analysis.verdict
        # Style follows the direction the coin would actually be traded in: a breakout
        # resolves its own, and may do so where the trend rule resolved nothing.
        style, _label = _DIRECTION_STYLE[analysis.setup_direction]
        plan = plans.get(analysis.symbol)
        if plan is not None:
            levels = [
                f"{plan.entry:g}",
                f"{plan.stop_loss:g}",
                f"{plan.take_profit:g}",
                f"{plan.risk_reward:.2f}",
                # The ratio alone says nothing: the planner floors every target at it. What
                # distinguishes the setups is whether that target is a level the market has
                # respected or one placed to satisfy the arithmetic.
                "structural" if plan.target_is_structural else "manufactured",
            ]
        else:
            levels = ["—"] * 5

        decision = (
            _DIRECTION_STYLE[verdict.decision][1]
            if verdict is not None and verdict.decision is not Direction.NONE
            else "WAIT"
        )
        cells = [
            str(position),
            analysis.symbol,
            verdict.trend.value if verdict is not None else analysis.direction.value,
            _setups_cell(analysis),
            (
                f"{verdict.entry_status.value} "
                f"({verdict.conditions_met}/{verdict.conditions_evaluated})"
            )
            if verdict is not None
            else "—",
            decision,
            *(levels if not show_all else []),
        ]
        if show_all or details:
            # The checklist explains a WAIT; the direction rule explains why a coin never
            # reached the checklist at all ("btc_veto", "lead_unresolved"). Both are reasons
            # the coin is not a trade, and dropping either leaves a silent row.
            reason = verdict.reason if verdict is not None else (analysis.reason or "—")
            cells.append(reason)
            cells.append("limited" if analysis.limited_history else "—")
        table.add_row(*cells, style=style or None)

    console.print(table)

    if not rows and not show_all:
        # Silence is a result, and saying so is the difference between "the method found
        # nothing today" and "something went wrong". Never dress a WAIT list as a trade list.
        waiting = sum(
            1 for a in run.analyses if a.verdict is not None and a.verdict.strategy is not None
        )
        console.print(
            f"[yellow]Nothing is ready to trade.[/yellow] "
            f"{waiting} coin(s) match a setup but are waiting on an entry; "
            f"re-run with --all to see what each is waiting for."
        )


def _describe_trendline(analysis: CoinAnalysis) -> str:
    """The fitted trendline as one readable phrase for the analyst's evidence."""

    line = analysis.trendline
    assert line is not None  # guarded by the caller
    shape = "rising" if line.is_rising else ("falling" if line.is_falling else "flat")
    return (
        f"{shape} at {line.level_now:.6g} "
        f"({line.touches} touches, fit {line.r_squared:.2f})"
    )


def build_scan_briefs(
    run: AnalysisRun, config: Config, cooldown: CooldownState | None = None
) -> tuple[SetupBrief, ...]:
    """Select the run's reviewable candidates and build their evidence packs.

    Every scored, directional coin is joined to its per-coin analysis, filtered by the
    configured score floor, dropped if already reviewed on the current reference candle,
    capped, then projected into briefs. The pool is the run's surfaced coins — those that
    resolved a direction and produced a usable plan — see
    :func:`~trader.candidate_gate.pair_candidates`. When a ``cooldown`` is supplied the
    selection is recorded into it, so a repeating watch loop that passes the same instance
    every iteration selects a given setup once per reference candle rather than once per
    poll.
    """

    selected = select(
        pair_candidates(
            run,
            long_only=config.long_only,
            review_within=config.strategies.review_within if config.strategies.enabled else None,
        ),
        # The 0-100 floor applies to the momentum scanner, which still has a score. The trend
        # engine's floor is ``strategies.review_within``, applied above in checklist terms.
        min_score=None,
        max_candidates=config.ai.max_candidates,
        cooldown=cooldown,
        reference_timeframe=config.reference_timeframe,
        reserve_per_direction=config.ai.reserve_per_direction,
    )
    if cooldown is not None:
        record_selection(cooldown, selected, config.reference_timeframe)
    return tuple(
        build_brief(
            candidate.symbol,
            candidate.direction,
            features_by_tf=candidate.analysis.features_by_tf,
            structure_by_tf=candidate.analysis.structure_by_tf,
            close_by_tf={
                tf: candles.latest_close
                for tf, candles in candidate.analysis.candles_by_tf.items()
            },
            order_book=candidate.analysis.order_book,
            plan=candidate.analysis.plan,
            limited_history=candidate.analysis.limited_history,
            timeframes=config.timeframes,
            verdict=candidate.analysis.verdict,
            verdicts=candidate.analysis.verdicts,
            patterns_by_tf={config.reference_timeframe: candidate.analysis.reaction_patterns},
            recent_candles=candidate.analysis.candles_by_tf.get(config.reference_timeframe),
            candle_count=(
                config.strategies.evidence_candles
                if config.strategies.enabled or config.breakout.enabled
                else 0
            ),
            trendline_by_tf=(
                {config.reference_timeframe: _describe_trendline(candidate.analysis)}
                if candidate.analysis.trendline is not None
                else None
            ),
        )
        for candidate in selected
    )


def _render_ai_dry_run(
    run: AnalysisRun,
    config: Config,
    console: Console,
    cooldown: CooldownState | None = None,
) -> None:
    """Print the evidence the AI analyst would be sent, without contacting a model.

    This is the "show me what this would cost and what it would say" switch: it exercises
    the real selection and evidence path and prints the result verbatim. When the stage is
    disabled in configuration there is nothing to preview, and saying so plainly beats
    printing an empty block.
    """

    if not config.ai.enabled:
        console.print(
            "[yellow]AI analyst is disabled (set `ai.enabled: true` to preview its "
            "evidence).[/yellow]"
        )
        return

    briefs = build_scan_briefs(run, config, cooldown)
    console.print(
        f"\n[bold]AI analyst evidence (dry run — no request made)[/bold]\n"
        # No score floor is quoted: the trend engine has no 0-100 total to floor since its
        # quality score was retired, so advertising one would describe a filter that is not
        # running. ``ai.min_score`` still applies to the momentum scanner.
        f"[dim]at most {config.ai.max_candidates} candidate(s) per run, "
        f"one review per {config.reference_timeframe} candle[/dim]"
    )
    # Printed with markup and highlighting off: the evidence is verbatim what a model
    # would receive, and its "[1] SYMBOL" candidate headers would otherwise be parsed as
    # rich markup tags.
    console.print(
        render_briefs(briefs, MarketBrief(btc_direction=run.btc_direction)),
        markup=False,
        highlight=False,
    )


# How each analyst action is coloured and labelled in the verdict table.
_ACTION_STYLE: dict[Action, tuple[str, str]] = {
    Action.LONG: ("green", "▲ LONG"),
    Action.SHORT: ("red", "▼ SHORT"),
    Action.WAIT: ("yellow", "… WAIT"),
    Action.AVOID: ("dim", "✕ AVOID"),
}

# Shown with every set of AI verdicts. The stage cannot place orders and never will in
# this version; saying so on every render is cheaper than one misunderstanding.
_AI_DISCLAIMER = (
    "Advisory only — these are a language model's opinions on the engine's own "
    "shortlist. No order has been placed and none can be. Verify before risking money."
)


def _levels(verdict: AnalystVerdict) -> tuple[str, str, str]:
    """Entry / stop / targets as display strings; empty dashes for a stand-aside verdict."""

    if not verdict.is_actionable:
        return "—", "—", "—"
    entry = f"{verdict.entry:g}" if verdict.entry is not None else "—"
    stop = f"{verdict.stop_loss:g}" if verdict.stop_loss is not None else "—"
    targets = " / ".join(f"{target:g}" for target in verdict.take_profits) or "—"
    return entry, stop, targets


def _render_verdicts(
    accepted: Sequence[AnalystVerdict],
    rejected: Sequence[RejectedVerdict],
    review: AnalystReview,
    console: Console,
    run: AnalysisRun | None = None,
) -> None:
    """Render the analyst's opinions beside what the engine thought.

    Disagreements are marked explicitly rather than quietly resolved: when the model says
    short and the engine said long, the trader needs to see the conflict, not an
    averaged-out answer. Rejected verdicts are listed separately with the reason they were
    not shown as tradeable — never as a plan.

    ``run`` supplies the engine's own checklist verdict so the two readings sit side by
    side. Neither overrides the other: a mechanical checklist can tick every box in a
    context that is obviously wrong, and it can be one condition short of a setup the model
    can see completing. Showing both is also the only way to learn, later, which is worth
    listening to.
    """

    # Only the scannable columns go in the table. Rationale and invalidation are prose and
    # were being crushed into ~8-character columns, which made the whole table unreadable —
    # they are printed underneath each verdict instead, where they have the full width.
    table = Table(title="AI analyst verdicts")
    table.add_column("Symbol")
    table.add_column("Action")
    table.add_column("Conf", justify="right")
    table.add_column("Entry", justify="right")
    table.add_column("SL", justify="right")
    table.add_column("TP", justify="right")
    table.add_column("Engine")
    table.add_column("Analyst")
    table.add_column("vs engine")

    verdicts_by_symbol = (
        {a.symbol: a.verdict for a in run.analyses if a.verdict is not None}
        if run is not None
        else {}
    )

    # Ordered by the analyst's confidence: it is the only figure that ranks the *actionable*
    # list, and without it the table would arrive in whatever order the model replied.
    for verdict in sorted(accepted, key=lambda v: (-v.confidence, v.symbol)):
        style, label = _ACTION_STYLE[verdict.action]
        entry, stop, targets = _levels(verdict)
        engine = verdicts_by_symbol.get(verdict.symbol)
        engine_read = (
            f"{engine.strategy.value if engine.strategy else 'none'} "
            f"{engine.entry_status.value} ({engine.conditions_met}/"
            f"{engine.conditions_evaluated})"
            if engine is not None
            else "—"
        )
        analyst_read = f"{verdict.strategy} {verdict.entry_status}"
        # Two ways to disagree, and they are different: about the trade, and about the read.
        conflicts = [] if verdict.agrees_with_engine else ["direction"]
        if engine is not None and verdict.entry_status != engine.entry_status.value:
            conflicts.append("entry")
        if engine is not None and engine.strategy is not None:
            if verdict.strategy != engine.strategy.value:
                conflicts.append("setup")
        table.add_row(
            verdict.symbol,
            label,
            f"{verdict.confidence:.0f}",
            entry,
            stop,
            targets,
            engine_read,
            analyst_read,
            "DISAGREES: " + ", ".join(conflicts) if conflicts else "—",
            style=style or None,
        )

    console.print(table)

    for verdict in accepted:
        console.print(f"\n[bold]{verdict.symbol}[/bold] — {verdict.rationale}", highlight=False)
        if verdict.invalidation:
            console.print(f"  [dim]invalid if:[/dim] {verdict.invalidation}", highlight=False)
        if verdict.key_risks:
            console.print(
                f"  [dim]risks:[/dim] {'; '.join(verdict.key_risks)}", highlight=False
            )

    if review.concentration_warning:
        console.print(
            f"\n[bold yellow]Concentration warning:[/bold yellow] "
            f"{review.concentration_warning}",
            highlight=False,
        )

    if rejected:
        console.print("\n[bold red]Rejected verdicts (not tradeable):[/bold red]")
        for item in rejected:
            console.print(
                f"  [red]{item.verdict.symbol}[/red] "
                f"{item.verdict.action.value.upper()} — {item.reason}",
                highlight=False,
            )

    cost = (
        f"${review.estimated_cost_usd:.4f}"
        if review.estimated_cost_usd is not None
        else "not configured (set ai.input_cost_per_mtok / ai.output_cost_per_mtok)"
    )
    console.print(
        f"[dim]model {review.model} · {review.prompt_tokens} prompt + "
        f"{review.completion_tokens} completion tokens · estimated cost {cost}[/dim]",
        highlight=False,
    )
    console.print(f"[dim]{_AI_DISCLAIMER}[/dim]")


def _load_credentials_file(env_file: str, console: Console) -> None:
    """Fill missing credentials from an environment file, reporting what it supplied.

    Called before any credential is resolved. This is what makes a scheduled run work:
    ``cron`` and ``launchd`` start with no login shell, so a key exported from a shell
    profile is simply absent there, while a file in the working directory is not.

    Only the *names* it set are printed, never the values.
    """

    loaded = load_env_file(env_file)
    if loaded:
        console.print(
            f"[dim]Loaded {', '.join(sorted(loaded))} from {env_file}[/dim]",
            highlight=False,
        )


def _build_notifier(config: Config) -> Notifier:
    """Construct the configured notifier, reading its credentials from the environment.

    Raises :class:`~trader.config.ConfigError` naming whichever variable is missing, so
    the caller can exit before fetching anything. Alerting disabled yields a notifier that
    delivers nothing, which keeps the calling code branch-free.
    """

    if not config.telegram.enabled:
        return NullNotifier()
    bot_token, chat_id = resolve_telegram_credentials(config.telegram)
    return TelegramNotifier(bot_token, chat_id)


def _build_analyst(config: Config) -> Analyst:
    """Construct the configured analyst, reading its credential from the environment.

    Raises :class:`~trader.config.ConfigError` when the credential is missing, so the
    caller can exit before fetching anything. Only OpenAI is implemented; an unsupported
    provider is already rejected at configuration load.
    """

    api_key = resolve_ai_credential(config.ai)
    return OpenAIAnalyst(
        openai_completion_fn(api_key),
        model=config.ai.model,
        temperature=config.ai.temperature,
        max_retries=config.ai.max_retries,
        input_cost_per_mtok=config.ai.input_cost_per_mtok,
        output_cost_per_mtok=config.ai.output_cost_per_mtok,
    )


def _send_alerts(
    notifier: Notifier | None,
    accepted: Sequence[AnalystVerdict],
    briefs: Sequence[SetupBrief],
    review: AnalystReview,
    config: Config,
    console: Console,
    scanner: str = SCANNER_SCAN,
) -> int:
    """Push every alert-worthy verdict, returning how many were sent.

    A delivery failure is reported and the loop continues: one unreachable send must not
    swallow the remaining alerts, and none of it may abort the run. Only actionable
    verdicts at or above the confidence floor are sent — see
    :func:`~trader.notify.should_alert`.
    """

    if notifier is None:
        return 0

    briefs_by_symbol = {brief.symbol: brief for brief in briefs}
    sent = 0
    for verdict in accepted:
        if not should_alert(verdict, config.telegram.min_confidence):
            continue
        brief = briefs_by_symbol.get(verdict.symbol)
        if brief is None:
            continue
        text = format_alert(
            verdict,
            brief,
            concentration_warning=review.concentration_warning,
            scanner=scanner,
        )
        try:
            notifier.send(text)
        except NotifyError as exc:
            console.print(
                f"[yellow]Could not alert for {verdict.symbol}: {exc}[/yellow]",
                highlight=False,
            )
            continue
        sent += 1

    if sent:
        console.print(f"[dim]Sent {sent} alert(s).[/dim]", highlight=False)
    return sent


def _review_candidates(
    briefs: Sequence[SetupBrief],
    market: MarketBrief,
    config: Config,
    console: Console,
    analyst: Analyst,
    *,
    scanner: str,
    decision_log: DecisionLog | None = None,
    notifier: Notifier | None = None,
    run: AnalysisRun | None = None,
) -> tuple[AnalystReview, tuple[AnalystVerdict, ...], tuple[RejectedVerdict, ...]] | None:
    """Review, validate, record, render and alert. ``None`` when nothing was reviewed.

    Shared by both scanners — only the evidence and the ``scanner`` label differ, and
    running them through one path is what keeps their behaviour (and their measurability)
    identical.

    Every failure here is contained: the engine's own table has already been printed and
    is the thing the trader paid to compute, so an analyst outage is reported and the run
    still succeeds. This is the only place that policy lives.

    A failed review still writes one record per selected candidate. A gap in the log would
    quietly bias any later measurement toward the runs that happened to work, so "we asked
    and got nothing" has to be as visible in the history as a verdict is.
    """

    if not briefs:
        return None

    try:
        review = analyst.review(briefs, market)
    except AnalystError as exc:
        console.print(f"[yellow]AI analyst unavailable: {exc}[/yellow]", highlight=False)
        _record_decisions(
            decision_log,
            records_for_failure(scanner, briefs, str(exc), config.ai.model),
            console,
        )
        return None

    accepted, rejected = partition_verdicts(review, briefs)
    _render_verdicts(accepted, rejected, review, console, run=run)
    # Recorded before alerting: the measurement history is the durable artefact, and it
    # should survive even if the phone never gets the message.
    _record_decisions(
        decision_log,
        records_for_review(scanner, briefs, review, accepted, rejected),
        console,
    )
    _send_alerts(notifier, accepted, briefs, review, config, console, scanner)
    return review, accepted, rejected


def _run_ai_review(
    run: AnalysisRun,
    config: Config,
    console: Console,
    analyst: Analyst,
    cooldown: CooldownState | None = None,
    decision_log: DecisionLog | None = None,
    notifier: Notifier | None = None,
) -> tuple[AnalystReview, tuple[AnalystVerdict, ...], tuple[RejectedVerdict, ...]] | None:
    """Select the trend scan's candidates and put them through the shared review path."""

    return _review_candidates(
        build_scan_briefs(run, config, cooldown),
        MarketBrief(btc_direction=run.btc_direction),
        config,
        console,
        analyst,
        scanner=SCANNER_SCAN,
        decision_log=decision_log,
        notifier=notifier,
        run=run,
    )


def _record_decisions(
    decision_log: DecisionLog | None,
    records: Sequence[DecisionRecord],
    console: Console,
) -> None:
    """Append records to the decision log, reporting but never re-raising a write error.

    A full disk must not cost the trader a scan they already paid for; it is reported so
    the gap in the measurement history is at least visible at the time it happens.
    """

    if decision_log is None or not records:
        return
    try:
        decision_log.append_all(records)
    except OSError as exc:
        console.print(
            f"[yellow]Could not write the decision log ({decision_log.path}): {exc}[/yellow]",
            highlight=False,
        )


def review_to_dict(
    review: AnalystReview,
    accepted: Sequence[AnalystVerdict],
    rejected: Sequence[RejectedVerdict],
) -> dict[str, object]:
    """Serialize a review into a JSON-ready dict mirroring the rendered table."""

    def verdict_dict(verdict: AnalystVerdict) -> dict[str, object]:
        return {
            "symbol": verdict.symbol,
            "action": verdict.action.value,
            "confidence": verdict.confidence,
            "entry": verdict.entry,
            "stop_loss": verdict.stop_loss,
            "take_profits": list(verdict.take_profits),
            "invalidation": verdict.invalidation,
            "rationale": verdict.rationale,
            "key_risks": list(verdict.key_risks),
            "agrees_with_engine": verdict.agrees_with_engine,
        }

    return {
        "model": review.model,
        "prompt_tokens": review.prompt_tokens,
        "completion_tokens": review.completion_tokens,
        "estimated_cost_usd": review.estimated_cost_usd,
        "concentration_warning": review.concentration_warning,
        "verdicts": [verdict_dict(verdict) for verdict in accepted],
        "rejected": [
            {"verdict": verdict_dict(item.verdict), "reason": item.reason}
            for item in rejected
        ],
    }


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
            # Whether the target is a swing level or one placed to satisfy the ratio. The
            # ratio itself is floored, so it is this flag that carries the information.
            "target_is_structural": plan.target_is_structural,
        }

    return {
        "coins": [
            {
                "symbol": a.symbol,
                "direction": a.direction.value,
                "surfaced": a.symbol in surfaced,
                "reason": a.reason,
                "limited_history": a.limited_history,
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
    dry_run_ai: bool = False,
    cooldown: CooldownState | None = None,
    analyst: Analyst | None = None,
    decision_log: DecisionLog | None = None,
    notifier: Notifier | None = None,
) -> AnalysisRun:
    """Run the v2 pipeline over the watchlist and render the results.

    Both providers are injectable so the pipeline can be exercised in tests with no
    network, and ``sleep`` is injectable so tests avoid wall-clock throttling delays.
    The default view is the threshold-filtered short list of surfaced setups ranked by
    score; ``show_all`` widens it to every analyzed coin and ``details`` expands the
    per-category breakdown. When ``json_path`` is given, a structured record derived
    from the same in-memory result as the table is written there. Coins whose market
    data cannot be fetched are skipped and summarized rather than aborting the run.
    With ``dry_run_ai`` the evidence the AI analyst would be sent is printed after the
    table; no model is contacted and nothing is spent. ``cooldown`` carries the
    already-reviewed setups across calls so a repeating caller does not re-select the
    same setup on every poll; a single call may leave it ``None``. ``analyst`` is
    injected so tests can substitute a fake; omitting it disables the stage entirely.
    ``decision_log`` receives one record per reviewed candidate; omitting it records
    nothing. ``notifier`` receives one alert per high-confidence actionable verdict;
    omitting it sends nothing.
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
        atr_buffer=config.atr_buffer,
        target_rr=config.target_rr,
        reference_timeframe=config.reference_timeframe,
        long_only=config.long_only,
        lead_timeframe=config.lead_timeframe,
        htf_timeframes=tuple(config.htf_timeframes),
        require_confirmation=config.require_confirmation,
        btc_veto=config.btc_veto,
        strategies=config.strategies,
        breakout=config.breakout,
    )
    _render_setups(result, console, details=details, show_all=show_all)
    _render_failures(result.failures, console)
    # The dry run and the real review are mutually exclusive: the whole point of the dry
    # run is that it spends nothing.
    # No analyst supplied means nothing to review — the stage stays silent rather than
    # printing an empty verdict table.
    review_payload: dict[str, object] | None = None
    if dry_run_ai:
        _render_ai_dry_run(result, config, console, cooldown)
    elif config.ai.enabled and analyst is not None:
        outcome = _run_ai_review(
            result, config, console, analyst, cooldown, decision_log, notifier
        )
        if outcome is not None:
            review, accepted, rejected = outcome
            review_payload = review_to_dict(review, accepted, rejected)

    if json_path is not None:
        payload = analysis_run_to_dict(result)
        if review_payload is not None:
            payload["review"] = review_payload
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
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
    dry_run_ai: bool = False,
    analyst: Analyst | None = None,
    decision_log: DecisionLog | None = None,
    notifier: Notifier | None = None,
    sleep: SleepFn = time.sleep,
    max_iterations: int | None = None,
) -> int:
    """Re-run the v2 pipeline on a timer, sleeping ``interval`` between runs.

    Loops forever by default; ``max_iterations`` bounds it so tests can assert the loop
    behavior without wall-clock timing. Returns the number of runs performed.

    The loop owns one :class:`~trader.candidate_gate.CooldownState` for its whole life and
    passes it into every iteration. That single shared instance is what makes the AI
    analyst affordable to leave running: a setup is selected once per reference candle, not
    once per poll, so shortening the poll interval costs nothing extra.
    """

    cooldown = CooldownState()
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
            dry_run_ai=dry_run_ai,
            cooldown=cooldown,
            analyst=analyst,
            decision_log=decision_log,
            notifier=notifier,
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
    ai: bool | None = typer.Option(
        None,
        "--ai/--no-ai",
        help=(
            "Enable or disable the AI analyst for this run, overriding `ai.enabled` in "
            "the configuration. Enabling it makes a paid request per run."
        ),
    ),
    env_file: str = typer.Option(
        DEFAULT_ENV_FILE,
        "--env-file",
        help=(
            "File of KEY=VALUE credential lines to fill any unset environment variables "
            "from. Anything already exported wins. Missing file is fine."
        ),
    ),
    dry_run_ai: bool = typer.Option(
        False,
        "--dry-run-ai",
        help=(
            "Print the evidence the AI analyst would be sent for this run's selected "
            "candidates. Contacts no model and spends nothing."
        ),
    ),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help=_VERBOSE_HELP),
    log_file: str | None = typer.Option(None, "--log-file", help=_LOG_FILE_HELP),
) -> None:
    """Score each watchlist coin from live Binance + TradingView and surface setups."""

    configure_logging(verbose, log_file)
    console = make_console()
    # The credential check runs inside this block, after the --ai/--no-ai override has
    # resolved and *before* any market data is fetched: a missing key should cost the
    # trader a second, not a full scan.
    try:
        config = load_config(config_path)
        if ai is not None:
            config = replace(config, ai=replace(config.ai, enabled=ai))
        reviewing = config.ai.enabled and not dry_run_ai
        if reviewing:
            _load_credentials_file(env_file, console)
        analyst = _build_analyst(config) if reviewing else None
        decision_log = DecisionLog(config.ai.decision_log) if reviewing else None
        notifier = _build_notifier(config) if reviewing else None
    except ConfigError as exc:
        typer.secho(f"Configuration error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    market_data_provider = CcxtBinanceProvider()
    analysis_provider = TradingViewProvider(batch_size=config.tradingview_batch_size)
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
            dry_run_ai=dry_run_ai,
            analyst=analyst,
            decision_log=decision_log,
            notifier=notifier,
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
            dry_run_ai=dry_run_ai,
            analyst=analyst,
            decision_log=decision_log,
            notifier=notifier,
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
    table.add_column("#", justify="right")
    table.add_column("Symbol")
    table.add_column("Dir")
    add_numeric_column(table, "Score")
    add_numeric_column(table, "Entry")
    add_numeric_column(table, "SL")
    add_numeric_column(table, "TP")
    add_numeric_column(table, "R:R")
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


def build_movers_briefs(
    run: MoversRun, config: Config, cooldown: CooldownState | None = None
) -> tuple[SetupBrief, ...]:
    """Select the momentum run's reviewable movers and build their evidence packs.

    Only surfaced movers are eligible, and the same score floor, per-run cap and
    per-candle cooldown apply as on the trend path — the floor now measured against the
    composite momentum score rather than the trend engine's quality score.
    """

    selected = select(
        pair_movers(run),
        min_score=config.ai.min_score,
        max_candidates=config.ai.max_candidates,
        cooldown=cooldown,
        reference_timeframe=DEFAULT_MOVERS_TIMEFRAME,
        reserve_per_direction=config.ai.reserve_per_direction,
    )
    if cooldown is not None:
        record_selection(cooldown, selected, DEFAULT_MOVERS_TIMEFRAME)
    return tuple(
        build_mover_brief(
            candidate.score,
            candles=candidate.coin.candles,
            order_book=candidate.coin.order_book,
            plan=candidate.coin.plan,
            breakout_level=trailing_breakout_level(
                candidate.coin.candles,
                config.breakout_lookback_days,
                candidate.score.direction,
            ),
        )
        for candidate in selected
    )


def _movers_market_brief(run: MoversRun) -> MarketBrief:
    """Market context for a momentum run: the benchmark its scores were measured against."""

    return MarketBrief(btc_return_pct=run.btc_return * 100.0)


def _render_movers_ai_dry_run(
    run: MoversRun,
    config: Config,
    console: Console,
    cooldown: CooldownState | None = None,
) -> None:
    """Print the evidence the analyst would be sent for this momentum run."""

    if not config.ai.enabled:
        console.print(
            "[yellow]AI analyst is disabled (set `ai.enabled: true` to preview its "
            "evidence).[/yellow]"
        )
        return

    briefs = build_movers_briefs(run, config, cooldown)
    console.print(
        f"\n[bold]AI analyst evidence (dry run — no request made)[/bold]\n"
        f"[dim]momentum floor {config.ai.min_score:g}, at most "
        f"{config.ai.max_candidates} candidate(s) per run, one review per "
        f"{DEFAULT_MOVERS_TIMEFRAME} candle[/dim]"
    )
    console.print(
        render_briefs(
            briefs,
            _movers_market_brief(run),
            scanner=SCANNER_MOVERS,
            breakdown_label="factors",
        ),
        markup=False,
        highlight=False,
    )


def run_movers_cli(
    market_data_provider: MarketDataProvider,
    console: Console,
    config: Config,
    sleep: SleepFn = time.sleep,
    *,
    show_all: bool = False,
    json_path: str | None = None,
    dry_run_ai: bool = False,
    cooldown: CooldownState | None = None,
    analyst: Analyst | None = None,
    decision_log: DecisionLog | None = None,
    notifier: Notifier | None = None,
) -> MoversRun:
    """Run the momentum scanner over the watchlist and render the ranked movers.

    The market-data provider is injectable so the pipeline can be exercised in tests
    with no network, and ``sleep`` is injectable so tests avoid wall-clock throttling.
    The BTC benchmark is derived from the same provider (fetched once per run).
    ``show_all`` widens the table to every evaluated coin; when ``json_path`` is given a
    structured record derived from the same in-memory result is written there.

    The AI-analyst collaborators mirror the trend path exactly: ``dry_run_ai`` previews
    the evidence without contacting a model, and ``analyst`` / ``decision_log`` /
    ``notifier`` / ``cooldown`` are injected so the whole stage is testable without a
    network. Omitting any of them disables that part of the stage.
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

    review_payload: dict[str, object] | None = None
    if dry_run_ai:
        _render_movers_ai_dry_run(run, config, console, cooldown)
    elif config.ai.enabled and analyst is not None:
        outcome = _review_candidates(
            build_movers_briefs(run, config, cooldown),
            _movers_market_brief(run),
            config,
            console,
            analyst,
            scanner=SCANNER_MOVERS,
            decision_log=decision_log,
            notifier=notifier,
        )
        if outcome is not None:
            review, accepted, rejected = outcome
            review_payload = review_to_dict(review, accepted, rejected)

    if json_path is not None:
        payload = movers_run_to_dict(run)
        if review_payload is not None:
            payload["review"] = review_payload
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
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
    ai: bool | None = typer.Option(
        None,
        "--ai/--no-ai",
        help=(
            "Enable or disable the AI analyst for this run, overriding `ai.enabled` in "
            "the configuration. Enabling it makes a paid request per run."
        ),
    ),
    env_file: str = typer.Option(
        DEFAULT_ENV_FILE,
        "--env-file",
        help=(
            "File of KEY=VALUE credential lines to fill any unset environment variables "
            "from. Anything already exported wins. Missing file is fine."
        ),
    ),
    dry_run_ai: bool = typer.Option(
        False,
        "--dry-run-ai",
        help=(
            "Print the evidence the AI analyst would be sent for this run's selected "
            "movers. Contacts no model and spends nothing."
        ),
    ),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help=_VERBOSE_HELP),
    log_file: str | None = typer.Option(None, "--log-file", help=_LOG_FILE_HELP),
) -> None:
    """Rank watchlist coins by momentum (relative strength, breakout, volume, accel)."""

    configure_logging(verbose, log_file)
    console = make_console()
    # Credentials are checked after the --ai/--no-ai override resolves and before any
    # market data is fetched, exactly as on the trend path.
    try:
        config = load_config(config_path)
        if ai is not None:
            config = replace(config, ai=replace(config.ai, enabled=ai))
        reviewing = config.ai.enabled and not dry_run_ai
        if reviewing:
            _load_credentials_file(env_file, console)
        analyst = _build_analyst(config) if reviewing else None
        decision_log = DecisionLog(config.ai.decision_log) if reviewing else None
        notifier = _build_notifier(config) if reviewing else None
    except ConfigError as exc:
        typer.secho(f"Configuration error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    market_data_provider = CcxtBinanceProvider()
    run_movers_cli(
        market_data_provider,
        console,
        config,
        show_all=show_all,
        json_path=json_path,
        dry_run_ai=dry_run_ai,
        analyst=analyst,
        decision_log=decision_log,
        notifier=notifier,
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
    console = make_console()
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
    report: BacktestReport,
    outcomes: tuple[TradeOutcome, ...],
    run: dict[str, object] | None = None,
) -> dict[str, object]:
    """Serialize the summary plus the full per-trade log into a JSON-ready dict.

    ``profit_factor`` of infinity (wins but no losing R) is emitted as ``null`` so the
    output is valid JSON. The trade log is derived from the same outcomes summarized into
    ``report``, so the export and the printed tables are a single source of truth.

    ``run`` records what produced the numbers — the traded window, the watchlist size and
    the cost settings. Without it a saved report is not safely comparable to anything: two
    runs over different windows or different fee assumptions produce different numbers for
    reasons that have nothing to do with the change under test, and a delta table would
    present that as a finding. Omitted only by callers that do not have the context.
    """

    pf = report.profit_factor
    payload: dict[str, object] = {
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
    if run is not None:
        payload["run"] = run
    return payload


def run_metadata(
    config: Config, start: int | None, end: int | None
) -> dict[str, object]:
    """Describe the run that produced a report, for comparability checks.

    The window is recorded in epoch milliseconds because that is what the backtester was
    actually given; a formatted date would invite a mismatch to slip through on a
    formatting difference.
    """

    bt = config.backtest
    return {
        "window_start_ms": start,
        "window_end_ms": end,
        "watchlist_size": len(config.watchlist),
        "timeframes": list(config.timeframes),
        "reference_timeframe": config.reference_timeframe,
        "lead_timeframe": config.lead_timeframe,
        "fee_rate": bt.fee_rate,
        "slippage": bt.slippage,
        "risk_per_trade": bt.risk_per_trade,
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


class BaselineError(Exception):
    """Raised when a baseline report cannot be loaded or is not comparable."""


# Metric labels and the number of decimals each is worth showing.
_DELTA_LABELS: dict[str, tuple[str, int]] = {
    "resolved_trades": ("Resolved trades", 0),
    "win_rate": ("Win rate %", 1),
    "expectancy": ("Expectancy (R)", 3),
    "profit_factor": ("Profit factor", 2),
    "max_drawdown": ("Max drawdown %", 1),
}

# Metrics stored as fractions but read by humans as percentages.
_PERCENT_METRICS = frozenset({"win_rate", "max_drawdown"})


def load_baseline_summary(path: str, run: dict[str, object]) -> dict[str, object]:
    """Load a saved report's summary, refusing one that is not comparable to this run.

    Comparability is checked on the traded window, the watchlist size and the cost
    settings, because a difference in any of those changes the numbers for reasons
    unrelated to whatever change is being measured. Silently comparing across them would
    manufacture a finding, which is worse than refusing.
    """

    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError as exc:
        raise BaselineError(f"baseline report not found: {path}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BaselineError(f"baseline report is not valid JSON: {path} ({exc})") from exc

    if not isinstance(payload, dict):
        raise BaselineError(f"baseline report is not an object: {path}")
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise BaselineError(f"baseline report has no 'summary' section: {path}")

    recorded = payload.get("run")
    if not isinstance(recorded, dict):
        raise BaselineError(
            f"baseline report predates run metadata and cannot be checked for "
            f"comparability: {path}. Re-generate it with --json."
        )

    mismatches = [
        f"{key}: baseline {recorded.get(key)!r} vs current {run.get(key)!r}"
        for key in ("window_start_ms", "window_end_ms", "watchlist_size", "fee_rate", "slippage")
        if recorded.get(key) != run.get(key)
    ]
    if mismatches:
        raise BaselineError(
            "baseline is not comparable to this run — " + "; ".join(mismatches)
        )
    return summary


def _format_metric(name: str, value: float | None, *, signed: bool = False) -> str:
    """Render one metric value at a sensible precision, as a percentage where apt."""

    if value is None:
        return "n/a"
    _label, places = _DELTA_LABELS.get(name, (name, 3))
    shown = value * 100.0 if name in _PERCENT_METRICS else value
    return f"{shown:+.{places}f}" if signed else f"{shown:.{places}f}"


def _render_comparison(deltas: Sequence[MetricDelta], baseline_path: str, console: Console) -> None:
    """Render the signed movement of each metric against the baseline.

    Improvement and regression are marked with distinct explicit words rather than colour
    alone, so the table still reads correctly piped to a file or in a log.
    """

    table = Table(title=f"Change vs baseline ({baseline_path})")
    table.add_column("Metric")
    table.add_column("Baseline", justify="right")
    table.add_column("This run", justify="right")
    table.add_column("Change", justify="right")
    table.add_column("Verdict")

    for delta in deltas:
        label, _places = _DELTA_LABELS.get(delta.name, (delta.name, 3))
        if delta.improved is True:
            verdict, style = "BETTER", "green"
        elif delta.improved is False:
            verdict, style = "WORSE", "red"
        else:
            verdict, style = "—", ""
        table.add_row(
            label,
            _format_metric(delta.name, delta.baseline),
            _format_metric(delta.name, delta.current),
            _format_metric(delta.name, delta.delta, signed=True),
            verdict,
            style=style or None,
        )

    console.print(table)


def _finish_backtest(
    report: BacktestReport,
    outcomes: tuple[TradeOutcome, ...],
    skipped: tuple[SkippedCoin, ...],
    console: Console,
    json_path: str | None,
    csv_path: str | None,
    run: dict[str, object] | None = None,
    baseline_path: str | None = None,
) -> None:
    """Render the report, optionally compare it to a baseline, and export it.

    The comparison is rendered before the exports so a baseline problem surfaces without
    the run's own results being lost.
    """

    _render_backtest_report(report, skipped, console)

    if baseline_path is not None and run is not None:
        summary = load_baseline_summary(baseline_path, run)
        current = backtest_report_to_dict(report, (), run)["summary"]
        assert isinstance(current, dict)
        _render_comparison(compare_summaries(summary, current), baseline_path, console)

    if json_path is not None:
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(backtest_report_to_dict(report, outcomes, run), handle, indent=2)
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
    _finish_backtest(
        report,
        run.outcomes,
        run.skipped,
        console,
        json_path,
        csv_path,
        run=run_metadata(config, None, None),
    )
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
    baseline: str | None = typer.Option(
        None,
        "--baseline",
        help=(
            "Path to a previously-written --json report. Prints a signed change table "
            "against it. Refuses baselines from a different window or cost settings."
        ),
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

    console = make_console()
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
    try:
        _finish_backtest(
            report,
            run.outcomes,
            run.skipped,
            console,
            json_path,
            csv_path,
            run=run_metadata(config, start, end),
            baseline_path=baseline,
        )
    except BaselineError as exc:
        typer.secho(f"Baseline error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc


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
    console = make_console()
    summary = CacheRefresher().refresh(config, provider, loader)
    _render_refresh_summary(summary, console)


if __name__ == "__main__":
    app()
