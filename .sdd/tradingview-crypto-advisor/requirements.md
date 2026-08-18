# Requirements: TradingView Crypto Advisory Bot

## Problem Statement

As a crypto trader, I want to know which coins are worth trading right now without manually opening TradingView, reading the technical-analysis gauge for each coin, and cross-checking it across several timeframes. Doing this by hand for a watchlist of coins is slow, repetitive, and easy to get wrong or skip — so I miss opportunities and act on incomplete information.

## Solution

A command-line advisory tool that, for a configurable watchlist of crypto coins, reads TradingView's own technical-analysis recommendation across multiple timeframes, combines those into a single weighted score per coin, and prints a ranked table of coins with clear buy/sell signals highlighted. It tells me *which coins to consider trading* and *why* (the per-timeframe breakdown), but it does not place any trades — a human stays in control. The tool is deliberately built so that a trade-execution layer can be added later without reworking the analysis core.

## User Stories

1. As a crypto trader, I want the bot to analyze a watchlist of coins in one command, so that I don't have to check each coin manually on TradingView.
2. As a crypto trader, I want each coin evaluated using TradingView's own technical-analysis recommendation, so that the insight reflects the analysis I already trust.
3. As a crypto trader, I want each coin analyzed across multiple timeframes, so that I can distinguish short-term noise from a durable trend.
4. As a crypto trader, I want longer timeframes to count for more than shorter ones in the final score, so that the recommendation favors more reliable signals.
5. As a crypto trader, I want a single weighted score per coin, so that I can compare coins against each other at a glance.
6. As a crypto trader, I want the coins presented as a ranked list from strongest buy to strongest sell, so that I can see the best opportunities first.
7. As a crypto trader, I want coins that cross a buy or sell threshold visually highlighted, so that I can immediately spot which coins are actionable.
8. As a crypto trader, I want to see the per-timeframe breakdown behind each score, so that I can understand and trust the reasoning before acting.
9. As a crypto trader, I want to define my own watchlist of coins in a configuration file, so that the bot analyzes exactly the coins I care about.
10. As a crypto trader, I want to configure which timeframes are used, so that I can tune the bot to my trading style.
11. As a crypto trader, I want to configure the weight given to each timeframe, so that I can control how much each timeframe influences the score.
12. As a crypto trader, I want to configure the buy and sell thresholds, so that I decide what counts as actionable.
13. As a crypto trader, I want to run the analysis once and get an immediate result, so that I can get a quick read whenever I want.
14. As a crypto trader, I want an optional watch mode that re-runs the analysis on an interval, so that I can keep an eye on the market without re-typing the command.
15. As a crypto trader, I want an option to show only the actionable (signal-crossing) coins, so that I can focus on a shortlist when the full ranking is too much.
16. As a crypto trader, I want the results optionally written out as structured JSON, so that I can feed them into other tools or keep a record.
17. As a crypto trader, I want a coin that fails to fetch to be skipped rather than aborting the whole run, so that one bad or delisted symbol doesn't cost me the entire analysis.
18. As a crypto trader, I want to see a summary of which coins failed and why at the end of a run, so that I know the result is partial and can investigate.
19. As a crypto trader, I want requests spaced out politely against TradingView, so that the tool remains reliable and doesn't get throttled or blocked.
20. As a crypto trader, I want default settings that work out of the box (Binance USDT pairs, sensible timeframes and weights), so that I can get value before doing any configuration.
21. As a crypto trader, I want to point the bot at a specific configuration file, so that I can maintain multiple profiles (e.g. scalping vs. swing).
22. As a crypto trader, I want the tool to be advisory-only for now, so that I retain full control over actual trades.
23. As a future user, I want the analysis engine to be cleanly separated from the interface and from execution, so that trade execution or other interfaces (Telegram, web) can be added later without rewriting the core.

## User Acceptance Tests

1. Given a valid configuration with a watchlist of coins, when I run the analysis once, then a ranked table of those coins is printed, sorted from strongest buy at the top to strongest sell at the bottom.
2. Given a coin whose weighted score is above the configured buy threshold, when the ranking is displayed, then that coin is visually highlighted as a buy signal.
3. Given a coin whose weighted score is below the configured sell threshold, when the ranking is displayed, then that coin is visually highlighted as a sell signal.
4. Given a coin whose score is between the thresholds, when the ranking is displayed, then that coin appears in the ranking but is shown as neutral (not highlighted as a signal).
5. Given the configured timeframes for a coin, when its score is computed, then longer timeframes contribute more to the score than shorter ones, according to the configured weights.
6. Given a displayed coin, when I look at its row, then I can see the per-timeframe recommendation breakdown that produced its score.
7. Given one coin in the watchlist that cannot be fetched (e.g. an invalid symbol or a network error), when I run the analysis, then the ranking is still produced for all coins that succeeded.
8. Given one or more coins failed during a run, when the run finishes, then a summary of the failed coins is shown.
9. Given the `--only-signals` option, when I run the analysis, then only coins that cross the buy or sell threshold are shown and neutral coins are omitted.
10. Given the JSON output option, when I run the analysis, then a structured JSON representation of the results is written to the expected location.
11. Given no custom configuration, when I run the analysis, then the bot uses its built-in defaults (Binance USDT pairs, default timeframes and weights) and produces a result.
12. Given a configuration file passed explicitly, when I run the analysis, then the settings in that file are used instead of the defaults.
13. Given watch mode with an interval, when I start the bot, then it re-runs the analysis repeatedly at that interval until I stop it.
14. Given an invalid configuration file (malformed or missing required values), when I run the analysis, then the bot reports a clear configuration error rather than producing a misleading result.
15. Given a watchlist larger than one coin, when I run the analysis, then requests are made sequentially with a delay between them rather than all at once.

## Definition of Done

- All user acceptance tests pass.
- The bot runs as a command-line tool and produces a ranked, signal-highlighted table for a configured watchlist.
- Multi-timeframe weighted scoring is implemented with longer timeframes weighted higher, and weights, timeframes, thresholds, and watchlist are all configurable via YAML.
- One-shot mode and an optional watch mode both work.
- `--only-signals` and JSON output options work.
- A failed coin is skipped gracefully and reported; a run never aborts because of a single failing symbol.
- Requests are throttled sequentially with a configurable delay.
- Sensible defaults allow the tool to run with no configuration file.
- Automated tests exist and pass for the scoring, signal-classification/ranking, and configuration logic, plus an orchestration test using a mocked data provider.
- The analysis core is importable independently of the CLI, so a future execution or alternative interface layer can reuse it.
- Project sets up and runs cleanly with the chosen tooling.

## Out of Scope

- Placing, managing, or simulating actual trades; any connection to an exchange for order execution.
- Storing exchange API keys or other trading secrets (a location is reserved for them, but nothing uses them yet).
- Persisting run history, trend analysis over time, and backtesting of signal quality.
- Dynamic discovery of coins (e.g. top-N by market cap or volume); the watchlist is fixed and user-defined.
- Alternative interfaces such as Telegram bots, push notifications, or a web dashboard.
- Concurrent/parallel fetching; v1 is deliberately sequential.
- Ingesting TradingView webhooks/alerts or Pine Script strategies.
- Computing indicators from raw OHLCV data independently of TradingView.

## Further Notes

- The data source is the `tradingview-ta` library, which surfaces TradingView's aggregated technical-analysis recommendation (STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL) per symbol and timeframe. It is unofficial and could change if TradingView changes its internals; the design isolates it behind a single interface to contain that risk.
- Default universe is Binance USDT pairs using the `crypto` screener; default timeframes are `15m, 1h, 4h, 1d`.
- The product is intentionally advisory-only for v1, but every decision favors a clean later path to trade execution.

---

## Technical Annex
> Written against codebase as of: 2026-07-28

This section contains the architectural and automated-testing decisions derived from the planning session. It is intended for architect and developer review. When this document is later used to generate tasks, each decision below will be verified against the current state of the codebase and any conflicts flagged before proceeding.

The codebase is greenfield as of this date — the repository contains only the original one-line `requirements.md` and a `.claude/` configuration directory. All modules below are new.

### Architectural Decisions

**Language & tooling**
- Python 3.11+.
- Dependency and environment management via `uv` with a `pyproject.toml`.
- Runtime dependencies: `tradingview-ta` (data), `PyYAML` (config), `typer` (CLI), `rich` (table rendering). Dev dependency: `pytest`.
- A `.env` location is reserved for future trading secrets but is unused in v1.

**Module structure** — the analysis core is importable independently of the CLI. Proposed package layout (names indicative, to be confirmed at task time):

- **Config module** — `load_config(path: str | None) -> Config`
  - Parses and validates YAML into a typed `Config` (e.g. a dataclass). Fields: `watchlist: list[str]`, `exchange: str` (default `BINANCE`), `screener: str` (default `crypto`), `timeframes: list[str]` (default `["15m","1h","4h","1d"]`), `weights: dict[str, float]` (per-timeframe, longer > shorter), `buy_threshold: float`, `sell_threshold: float`, `request_delay: float` (seconds), `watch_interval: float | None`.
  - Applies built-in defaults when no file or when fields are omitted; raises a clear, typed configuration error on malformed/invalid input.

- **AnalysisProvider** — the mockable network boundary; a thin interface (Protocol/ABC) with one concrete implementation over `tradingview-ta`.
  - `get_analysis(symbol: str, exchange: str, screener: str, interval: str) -> TimeframeResult`
  - `TimeframeResult` captures the interval, the recommendation label, and the underlying buy/neutral/sell indicator counts.
  - The concrete implementation wraps `tradingview_ta.TA_Handler` and maps the interval strings to the library's `Interval` constants.

- **ScoringEngine** — pure.
  - `score(symbol: str, results: list[TimeframeResult], weights: dict[str, float]) -> CoinScore`
  - Maps labels to numeric values `STRONG_BUY=+2, BUY=+1, NEUTRAL=0, SELL=-1, STRONG_SELL=-2`, multiplies by the timeframe weight, and combines into a single weighted score. `CoinScore` retains the per-timeframe breakdown for display.
  - No I/O; deterministic given inputs.

- **SignalClassifier / Ranker** — pure.
  - `rank(scores: list[CoinScore], buy_threshold: float, sell_threshold: float) -> Ranking`
  - Sorts descending by weighted score and tags each coin `BUY` (≥ buy threshold), `SELL` (≤ sell threshold), or `NEUTRAL`.

- **Runner / Orchestrator** — coordination.
  - `run(config: Config, provider: AnalysisProvider) -> RunResult`
  - Iterates watchlist × timeframes sequentially, sleeping `request_delay` between requests. Catches per-symbol exceptions, records them as failures, and continues (graceful degradation). Feeds successful results through `ScoringEngine` then `SignalClassifier`. Returns a `RunResult` holding the `Ranking` plus a list of failures (symbol + reason).
  - Watch mode is a thin loop around `run(...)` on `watch_interval`; external scheduling (cron) remains available for one-shot.

- **CLI / Presentation** — `typer` app.
  - Flags/options: `--config PATH`, `--watch [INTERVAL]`, `--only-signals`, `--json PATH`.
  - Renders the `Ranking` as a `rich` table: score, signal tag, and per-timeframe breakdown, with buy rows highlighted (e.g. green ▲) and sell rows (e.g. red ▼). Prints the failure summary at the end. Writes JSON to the given path when requested.
  - Contains no analysis logic — purely wiring, rendering, and I/O.

**Data flow:** CLI → `load_config` → `Runner.run(config, provider)` → per symbol: `provider.get_analysis` (×timeframes, throttled) → `ScoringEngine.score` → collect `CoinScore`s → `SignalClassifier.rank` → `RunResult` → CLI renders table + JSON + failure summary.

**Separation for future execution:** the analysis core (Config, AnalysisProvider, ScoringEngine, SignalClassifier, Runner) has no dependency on the CLI. A future execution layer would consume `RunResult`/`Ranking` and act on flagged signals; it is out of scope here but the boundary is preserved.

### Automated Testing Decisions

**What makes a good test here:** tests assert on external behavior (inputs → outputs of a module's public interface), not internal implementation details. The pure modules are the priority because they hold the numerical risk. The network is never hit in the test suite; it is replaced at the `AnalysisProvider` seam.

- **ScoringEngine (unit)** — fabricate `TimeframeResult` inputs and assert the weighted score, including that longer timeframes dominate per the configured weights, and edge cases (all-neutral, conflicting timeframes, missing timeframe).
- **SignalClassifier / Ranker (unit)** — assert correct descending ordering and correct BUY/SELL/NEUTRAL tagging around the threshold boundaries.
- **Config (unit)** — assert defaults are applied when fields are omitted, valid YAML parses to the expected `Config`, and malformed/invalid input raises a clear configuration error.
- **Runner (unit/integration with a fake provider)** — inject a mock/stub `AnalysisProvider` and assert: successful coins produce a ranking; a provider that raises for one symbol yields a partial ranking plus that symbol recorded as a failure (graceful degradation); requests are made per configured timeframe.

Framework: `pytest`. No live-network tests run by default; if a smoke test against a single real symbol is added, it is marked and skipped by default. CLI/Presentation and the real `tradingview-ta` provider are not unit-tested in v1 (thin, I/O- and network-bound).

**Prior art:** none — greenfield repository. These become the first test suite.
