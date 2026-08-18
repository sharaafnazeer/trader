# Requirements: Signal-Edge Backtester

> Builds on the `multi-factor-scoring-engine` and `configurable-direction-profiles`
> features. Those produce live directional setups with entry/stop/target but have never
> been validated against history, so the tool cannot honestly state a win rate. This
> feature replays the exact scoring engine over historical candles to *measure* the win
> rate, expectancy, and drawdown of its signals. It reuses the live analysis modules
> unchanged; it adds historical data, replay, trade simulation, and metrics.

## Problem Statement

As a crypto trader, I have a bot that produces confident-looking setups — a direction, a 0–100 score, an entry, a stop, and a target — but I have no idea whether any of it actually works. Nobody has ever checked its signals against real history, so "score 82, SHORT, 2.5:1" is a hypothesis, not evidence. I can't tell whether following these signals would have won 40% or 60% of the time, whether the winners paid for the losers, or how deep the losing streaks got. Without that, I'm risking money on an unproven rule, and any win-rate figure anyone gives me is a guess.

## Solution

A backtester that replays the exact live scoring engine across historical candles for the configured watchlist, treating every setup it would have produced as a hypothetical trade, and simulating that trade's outcome bar by bar to see whether the stop or the target was hit. It measures the results honestly — win rate, expectancy per trade, profit factor, average and worst win/loss, maximum drawdown, and an equity curve — after realistic fees and slippage, with per-coin and per-direction breakdowns and a full exportable log of every simulated trade. Crucially, at every historical moment the engine sees only data that existed then, so the results are not inflated by hindsight, and every ambiguous or unfinished case is resolved conservatively. The trader runs it from the command line over any date range, reusing the same configuration the live scanner uses, and gets a measured answer instead of a guess.

## User Stories

1. As a trader, I want the backtester to replay the exact same scoring engine the live scanner uses, so that the results reflect the real strategy and not a re-implementation.
2. As a trader, I want every historical evaluation to use only data available at that moment, so that the results are not inflated by look-ahead.
3. As a trader, I want each setup the engine would have surfaced to be recorded as a hypothetical trade, so that I can measure how those signals performed.
4. As a trader, I want each trade's outcome decided by walking price forward until the stop or the target is hit, so that wins and losses are based on what price actually did.
5. As a trader, I want stop/target hits detected on a finer timeframe, so that intrabar touches are captured accurately.
6. As a trader, I want a bar that could have hit both stop and target counted as a stop, so that results are conservative rather than optimistic.
7. As a trader, I want only one open trade per coin at a time, entered when a fresh setup appears, so that one persistent signal is not counted as many overlapping trades.
8. As a trader, I want trades still open when history runs out to be excluded from the win/loss stats and reported separately, so that no outcome is invented.
9. As a trader, I want an optional maximum holding period, so that I can cap how long a trade stays open before exiting at market.
10. As a trader, I want realistic fees and slippage applied to entries and exits, so that the win rate reflects real trading costs.
11. As a trader, I want to be able to set costs to zero, so that I can see the raw signal edge separately from cost drag.
12. As a trader, I want a win rate, so that I know how often the signals were right.
13. As a trader, I want expectancy (average result per trade) and profit factor, so that I can judge whether winners outweigh losers regardless of win rate.
14. As a trader, I want average and largest win and loss, so that I understand the shape of outcomes.
15. As a trader, I want a maximum drawdown, so that I know the worst losing stretch I would have endured.
16. As a trader, I want an equity curve from risking a fixed fraction per trade, so that I can visualize cumulative performance.
17. As a trader, I want a per-coin breakdown, so that I can see which coins the strategy works on.
18. As a trader, I want a per-direction (long vs short) breakdown, so that I can see whether it is better long or short.
19. As a trader, I want a full log of every simulated trade (coin, direction, entry and exit time and price, result, costs), so that I can audit and analyze the results myself.
20. As a trader, I want to run the backtest over a chosen date range, so that I can test different market periods.
21. As a trader, I want the backtest to reuse my live configuration (watchlist, profile, weights, threshold, direction rule), so that it measures exactly the engine I would run.
22. As a trader, I want historical candles fetched once and cached, so that repeated backtests are fast and do not re-download years of data.
23. As a trader, I want the backtest to work for both the futures and spot profiles, so that I can validate whichever style I trade.
24. As a trader, I want a coin without enough history for the chosen range to be handled gracefully and noted, so that thin data does not silently break or bias the run.
25. As a trader, I want the report to state clearly that past performance does not guarantee future results, so that I do not over-trust a single number.
26. As a trader, I want the backtester to change nothing about how the live scanner behaves, so that adding it introduces no regression.

## User Acceptance Tests

1. Given a historical date range and a watchlist, when the backtest runs, then it reports a win rate, expectancy, profit factor, maximum drawdown, and trade counts.
2. Given any historical evaluation moment, when the engine computes a signal, then it uses only candles that had closed at or before that moment (no future data).
3. Given a surfaced setup at a point in history, when price later reaches the target before the stop, then the trade is recorded as a win of its target size; when it reaches the stop first, then it is recorded as a loss.
4. Given a single finer-timeframe bar whose range spans both the stop and the target, when the outcome is resolved, then the trade is counted as a stop (loss).
5. Given a coin already in an open simulated trade, when the engine surfaces the same signal on the next bar, then no second trade is opened for that coin.
6. Given a trade still open when the history ends, when the report is produced, then that trade is excluded from win/loss statistics and counted separately as unresolved.
7. Given costs are configured, when results are shown, then the win rate and expectancy are net of fees and slippage; and given costs set to zero, then results reflect the gross edge.
8. Given the same run, when the trade log is exported, then it contains one row per simulated trade with coin, direction, entry/exit time and price, result, and costs, matching the summary totals.
9. Given a chosen date range, when the backtest runs, then only trades entered within that range are included, with earlier candles used solely to warm up the indicators.
10. Given a configuration file, when the backtest runs, then it uses the same watchlist, profile, weights, threshold, and direction rule as the live scanner would.
11. Given a backtest has been run once for a symbol and timeframe, when it is run again, then the cached candles are reused rather than re-downloaded.
12. Given a coin with insufficient history for the requested range, when the backtest runs, then that coin is handled gracefully (skipped or evaluated only where it has data) and noted, without aborting the run.
13. Given an equity curve is produced from a fixed fractional risk per trade, when the maximum drawdown is reported, then it corresponds to the largest peak-to-trough decline on that curve.

## Definition of Done

- All user acceptance tests pass.
- The backtester replays the live analysis modules unchanged; there is no separate re-implementation of indicators, direction, scoring, or trade levels.
- At every evaluation moment, only candles closed at or before that moment are visible to the engine (provable no look-ahead).
- Each surfaced setup becomes one hypothetical trade; outcomes are resolved on the finer timeframe with same-bar stop/target ambiguity counted as a stop.
- One open trade per coin at a time, entered on fresh qualification; unresolved (still-open at end of data) trades are excluded from win/loss stats and reported separately; an optional maximum holding period is supported.
- Fees and slippage are configurable and applied to entries and exits; results are net by default and gross when costs are set to zero.
- The report includes win rate, expectancy, profit factor, average and largest win/loss, maximum drawdown, resolved and unresolved counts, per-coin and per-direction breakdowns, and a fixed-fractional equity curve.
- A full per-trade log can be exported and reconciles with the summary totals.
- The backtest runs from a `backtest` command over a configurable date range, reusing the live configuration, and works for both futures and spot profiles.
- Historical candles are fetched via pagination and cached on disk; repeated runs reuse the cache.
- Coins with insufficient history are handled gracefully and noted; the run never aborts on one coin.
- The report carries a clear "past performance is not indicative of future results" disclaimer.
- The live scanner's behavior is unchanged; no regression in existing tests; all quality gates pass.

## Out of Scope

- Portfolio/capital simulation with shared capital, concurrent-position limits, and compounding (this measures signal edge with independent trades; portfolio modeling is a later phase).
- Parameter optimization / sweeping weights or thresholds to find best settings (overfitting risk; separate phase).
- Live forward-testing / paper trading and persistence of results over time.
- Trade execution and exchange keys.
- Any change to the scoring categories, direction rule, or trade-plan geometry.
- Statistical significance testing, Monte Carlo, or walk-forward optimization.
- A graphical chart of the equity curve (numeric series and drawdown are produced; plotting is out of scope).

## Further Notes

- This feature exists because the tool currently cannot state a win rate — every prior spec deferred backtesting. It replaces a guess with a measurement.
- The design is deliberately conservative so any reported edge is credible rather than oversold: no look-ahead (live-path replay), stop-first on ambiguous bars, unresolved trades excluded, and costs net by default. The intent is to *understate* rather than flatter.
- Sequencing: the backtester measures whatever engine is configured. It is most meaningful once `configurable-direction-profiles` is implemented, so it measures the usable direction rule rather than the strict rule that surfaces almost nothing. Recommended order: implement `configurable-direction-profiles` first, then this.
- A backtested edge still does not guarantee future profit; results depend on the period tested and exclude real-world frictions beyond modeled fees/slippage (liquidity gaps, funding, outages).

---

## Technical Annex
> Written against codebase as of: 2026-07-28

Builds on `src/trader/` (the `multi-factor-scoring-engine` implementation, and `configurable-direction-profiles` once implemented). When generating tasks, verify each decision against the current code and flag conflicts.

### Current state (baseline)

- Data seam `MarketDataProvider` (Protocol) + `CcxtBinanceProvider` in `market_data.py` — `get_ohlcv(symbol, timeframe, limit) -> Candles` (fetches recent candles only), `get_order_book(...)`. `Candles` wraps a pandas OHLCV frame with `.latest_close`.
- Pure analysis modules reused unchanged: `indicators.compute_features`, `structure.analyze`, `direction.decide`, `scoring_model.ScoringModel`, `trade_planner.TradePlanner`.
- `config.py` — frozen `Config` + `load_config` (ConfigError, unknown-key rejection, typed validators); `runner.py` `run_analysis`/`AnalysisRun`; `cli.py` `scan`/`market-data` commands (typer + rich).
- Tooling: Python ≥3.11, `uv`; `pytest`/`ruff`/`mypy --strict`; analysis core free of `typer`/`rich` (enforced by `tests/test_core_independence.py`).

### Architectural Decisions

**New modules (analysis-core, free of `typer`/`rich`).**

- **HistoricalDataProvider** — a Protocol plus a concrete CCXT implementation with an on-disk cache.
  - `get_history(symbol, timeframe, start, end) -> Candles` — pages `fetch_ohlcv` with a moving `since` to cover `[start − warmup, end]`; caches candles to disk (e.g. Parquet/CSV under a configurable cache dir) keyed by `(symbol, timeframe)`; incremental (only fetches gaps). Lazily imports `ccxt`. Returns the same `Candles` type so the analysis modules consume it unchanged.

- **Replay** (pure) — point-in-time slicing.
  - `slice_at(frames: dict[str, Candles], ts) -> dict[str, Candles]` — for each timeframe, returns a `Candles` containing only bars **closed at or before `ts`**. This is the single guardrail against look-ahead; the analysis modules only ever receive sliced frames.
  - Also yields the ordered sequence of reference-timeframe close timestamps that drive evaluation.

- **TradeSimulator** (pure) — outcome resolution.
  - `simulate(setup, finer_bars, *, fee_rate, slippage, max_holding_bars) -> TradeOutcome` — walks `finer_bars` (the smallest-timeframe candles after entry) forward; a bar whose low ≤ stop (long) marks a stop, high ≥ target marks a target; **if both in one bar → stop** (conservative). Applies costs to entry/exit; computes the realized **R-multiple**. Honors `max_holding_bars` (exit at market) and returns an `unresolved` outcome if neither level nor the time stop is reached before data ends.
  - `TradeOutcome` (frozen): symbol, direction, entry/exit time & price, result (`WIN`/`LOSS`/`UNRESOLVED`), r_multiple, costs.

- **Backtester** — orchestration.
  - `run(config, history_provider) -> BacktestRun` — per coin: fetch full history per timeframe (via `HistoricalDataProvider`); iterate reference-timeframe closes; at each, `Replay.slice_at` → run the *live* pipeline (`compute_features`/`structure`/`direction`/`ScoringModel`/`TradePlanner`) exactly as `scan` does; apply lifecycle (enter only when flat, on fresh qualification; ignore signals while in a trade); hand entered setups to `TradeSimulator`. Collects all `TradeOutcome`s into `BacktestRun`. BTC context is computed point-in-time from BTC's own sliced frames.

- **Metrics** (pure).
  - `summarize(trades, *, risk_per_trade) -> BacktestReport` — win rate, expectancy (mean R), profit factor, average/largest win & loss, max drawdown, resolved/unresolved counts; per-coin and per-direction breakdowns; a fixed-fractional equity curve (trades ordered by entry time, risking `risk_per_trade` of running equity), with max drawdown taken from that curve. Excludes `UNRESOLVED` trades from win/loss stats.

**Modified modules.**
- `config.py` — add a `backtest` section: `fee_rate`, `slippage`, `risk_per_trade` (default 0.01), `max_holding_bars` (default null/off), `start`/`end` dates, `cache_dir`. Typed validation; unknown-key rejection extended.
- `cli.py` — new `backtest` command: `--config`, `--from`, `--to`, `--json`, `--csv`. Renders the `BacktestReport` (rich tables) with the disclaimer; `--json`/`--csv` write the summary plus the full `TradeOutcome` log.

**Data flow (per coin):** `HistoricalDataProvider.get_history` (all timeframes, cached) → for each reference-tf close `ts`: `Replay.slice_at` → live engine (direction+score+plan) → lifecycle gate → on entry, `TradeSimulator.simulate` over finer-tf bars after `ts` → `TradeOutcome`. All coins' outcomes → `Metrics.summarize` → `BacktestReport` → CLI.

**Correctness stance (encodes planning decisions):**
- No look-ahead: analysis modules only ever see `Replay.slice_at` output; there is no parallel indicator path. The BTC market context is sliced point-in-time from BTC's own frames, never fetched whole.
- Liquidity category: no historical order book exists, so the backtest supplies a **neutral full-credit liquidity fraction** (configurable) to `ScoringModel`, keeping the 0–100 scale and the live quality threshold identical. Rationale: liquidity is a tradeability gate, not a directional predictor, and backtests run on liquid pairs — assuming it isolates the signal edge being measured. The report states that historical liquidity/spread is not modeled.
- Conservative resolution: same-bar stop+target → stop; unresolved trades excluded from win/loss; costs net by default (zero-cost reachable for gross edge).
- Determinism: no randomness; identical inputs → identical report.
- No regression: the reused analysis modules are not modified; existing `scan`/`market-data` behavior and tests are unchanged.

### Automated Testing Decisions

Test external behavior with pinned inputs; no network (mock `HistoricalDataProvider`; hand-built frames). `pytest`; prior art in `tests/test_runner_analysis.py` (mocked providers), `tests/test_trade_planner.py`/`tests/test_scoring_model.py` (pinned pure logic), `tests/test_config.py`.

- **Replay (unit) — the critical one.** With multi-timeframe frames and a chosen `ts`, assert every returned frame contains only bars closed ≤ `ts` (explicit no-look-ahead test), and that the reference-timeframe close sequence is correct.
- **TradeSimulator (unit).** Pinned finer-bar sequences: target-first → WIN with correct R; stop-first → LOSS; same-bar both → LOSS (stop-first tie-break); `max_holding_bars` → market exit; data ends first → UNRESOLVED; fees/slippage applied (net vs zero-cost gross).
- **Metrics (unit).** Pinned trade sets → win rate, expectancy, profit factor, avg/largest win-loss, max drawdown, equity curve; UNRESOLVED excluded from win/loss; per-coin/per-direction splits.
- **config (unit).** `backtest` section parses with defaults; invalid `fee_rate`/`risk_per_trade`/dates raise `ConfigError`; unknown keys rejected.
- **Backtester (integration, mocked history).** Small synthetic multi-timeframe history through the real analysis modules: asserts a known price path produces the expected trade(s) and that no evaluation used future bars (e.g. by constructing history where a future spike must not affect an earlier signal). Confirms lifecycle (one-position-per-coin, enter-on-transition) and reuse of the live pipeline.
- CLI/presentation, the concrete CCXT historical fetch over the wire, and the disk cache I/O are not unit-tested (thin / I/O-bound), consistent with the baseline; the cache key/incremental logic may get a light unit test with a fake fetch function.
