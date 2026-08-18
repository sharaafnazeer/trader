# Tasks: Signal-Edge Backtester

Feature slug: `signal-edge-backtester`
Requirements: `.sdd/signal-edge-backtester/requirements.md`

Each task is a vertical slice cutting end-to-end (historical data → point-in-time replay → live engine → trade simulation → metrics → CLI report). Tasks are in dependency order. New settings use in-code defaults in the task that introduces them, then move to YAML in the final task. The analysis modules (`indicators`, `structure`, `direction`, `scoring_model`, `trade_planner`) are reused **unchanged** — the backtester must not re-implement them. Quality gates are identical and deterministic: test suite, linter, type checker, byte-compile.

---

## Task 01-replay-skeleton

Prove the whole backtest pipe end-to-end for one coin with a provable no-look-ahead guarantee: march through a supplied multi-timeframe history, at each reference-timeframe close slice every timeframe to bars closed at or before that moment, run the *live* analysis pipeline on those truncated frames, enter one trade per coin on a fresh qualification, resolve its outcome on the finer timeframe, and print a trade count and win rate from a new `backtest` command. Data is injected/mocked (no live fetch yet).

### Implementation steps

- [x] Implement a pure `Replay` that, given full-history frames and a timestamp, returns per-timeframe frames containing only bars closed at or before that timestamp, and yields the ordered reference-timeframe close timestamps that drive evaluation.
- [x] Implement a minimal `TradeSimulator` that, given an entry setup (entry/stop/target) and the finer-timeframe bars after entry, resolves win/loss by first touch on highs/lows, counting a bar that spans both stop and target as a stop.
- [x] Implement a minimal `Backtester` that drives reference-timeframe closes for one coin, slices via `Replay`, runs the reused live pipeline (features → structure → direction → score → plan), enters one trade per coin only when flat and on a fresh qualification (a transition into a surfaced setup — not re-entering while the signal persists nor immediately after a trade closes), and simulates each trade.
- [x] Slice the BTC market context through `Replay` as well (point-in-time from BTC's own frames, never the whole/live frame), and — because no historical order book exists — supply a neutral full-credit liquidity fraction to the scoring model so a backtest score equals the live pipeline's score for the same sliced inputs; make the assumed liquidity value explicit and note the omission where results are reported.
- [x] Implement minimal `Metrics` (resolved trade count, win rate) and a new `backtest` CLI command that runs over an injected history and prints them.
- [x] Add unit tests, including an explicit no-look-ahead test (for both coin data and BTC context), and extend `tests/test_core_independence.py` to cover the new core modules.

### Acceptance criteria

- [x] Given multi-timeframe frames and a timestamp, `Replay` returns only bars closed at or before it, and a future spike placed after the timestamp provably does not change the sliced frames (explicit no-look-ahead test).
- [x] `TradeSimulator` returns a win when the target is touched before the stop, a loss when the stop is touched first, and a loss when one finer bar spans both (tested).
- [x] While a coin has an open trade a repeated qualifying signal does not open a second trade, and after a trade closes a still-qualifying signal does not immediately re-enter until a fresh transition into a setup occurs (one-position-per-coin, enter-on-transition, tested).
- [x] The BTC market context is computed from BTC's own `Replay`-sliced frames at each moment; a BTC price spike placed after an evaluation timestamp does not change that earlier evaluation (BTC point-in-time, tested).
- [x] With no historical order book, the scoring model receives a neutral full-credit liquidity fraction, so a coin's backtest score equals the live pipeline's score for the same sliced inputs (tested).
- [x] Running the `backtest` command over an injected history prints a resolved trade count and win rate, exit code 0.
- [x] Importing `Replay`, `TradeSimulator`, `Backtester`, and `Metrics` succeeds with `typer` and `rich` blocked (core-independence test passes).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 02-historical-data

Feed the backtester real, deep history: fetch historical candles for a date range plus indicator warm-up by paginating the exchange, cache them on disk, and reuse the cache on subsequent runs so repeated backtests are fast and don't re-download. A `backtest` run now sources genuine history per coin per timeframe.

### Implementation steps

- [x] Define a `HistoricalDataProvider` interface plus a concrete implementation that paginates the exchange (moving `since`) to cover the requested range and warm-up, importing the exchange library lazily.
- [x] Add an on-disk cache keyed by symbol and timeframe that is written after a fetch and read (incrementally) on later runs; only missing spans are fetched.
- [x] Wire the `Backtester` to obtain each coin's per-timeframe history from the provider.
- [x] Add unit tests using a fake/injected fetch function (no network): pagination beyond a single call, cache write-then-reuse, and graceful handling of a coin with no data.

### Acceptance criteria

- [x] Given a fake fetch that returns data in pages, the provider assembles a history longer than a single page for the requested range plus warm-up (tested).
- [x] After a first run populates the cache, a second run for the same symbol and timeframe reuses the cache and does not call the fetch again (tested with a call-counting fake).
- [x] A canned exchange-shaped response maps into the existing candle type with the expected rows/columns (tested).
- [x] A coin for which no history is available is added to an explicit skipped/failures collection and the run completes for the remaining coins without aborting (tested).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 03-trade-lifecycle-and-costs

Make each simulated trade realistic and honest: apply configurable fees and slippage to entry and exit (net by default, zero-cost toggle for the raw edge), support an optional maximum holding period that exits at market, and mark trades still open when history ends as unresolved and excluded from win/loss statistics.

### Implementation steps

- [x] Extend `TradeSimulator` to apply a fee rate and slippage to entry and exit and compute the realized R-multiple net of costs.
- [x] Add an optional maximum-holding-period exit (at market) and an unresolved outcome when neither level nor the time stop is reached before data ends.
- [x] Ensure the `Backtester` records unresolved trades separately and never invents an outcome.
- [x] Add unit tests for costs (net vs zero-cost gross), the time-stop exit, the unresolved case, and R-multiple correctness.

### Acceptance criteria

- [x] With costs configured, the realized R-multiple is reduced versus the same trade at zero cost, and setting costs to zero reproduces the gross result (both tested).
- [x] With a maximum holding period set, a trade that reaches neither level exits at market after that many bars (tested).
- [x] A trade still open when the finer-timeframe history ends is returned as unresolved and is excluded from win/loss counts (tested).
- [x] For a known price path, the winning-trade R-multiple equals the target-to-risk ratio net of the configured costs (tested).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 04-full-report-and-export

Turn the raw trades into the full honest edge report and make it auditable: win rate, expectancy, profit factor, average and largest win/loss, maximum drawdown, resolved and unresolved counts, per-coin and per-direction breakdowns, and a fixed-fractional equity curve — rendered to the terminal with a past-performance disclaimer, and exportable as a full per-trade log that reconciles with the summary.

### Implementation steps

- [x] Extend `Metrics` to compute expectancy, profit factor, average/largest win and loss, maximum drawdown, resolved/unresolved counts, per-coin and per-direction breakdowns, and a fixed-fractional equity curve (trades ordered by entry time), excluding unresolved trades from win/loss stats.
- [x] Render the report as terminal tables with the "past performance is not indicative of future results" disclaimer.
- [x] Add `--json` and `--csv` export of the summary plus the full per-trade log (coin, direction, entry/exit time and price, result, R-multiple, costs).
- [x] Add unit tests for each metric on pinned trade sets and for export/summary reconciliation.

### Acceptance criteria

- [x] On a pinned trade set, win rate, expectancy, profit factor, average/largest win and loss, and resolved/unresolved counts match hand-computed values (tested).
- [x] The maximum drawdown equals the largest peak-to-trough decline of the fixed-fractional equity curve for a pinned trade set (tested).
- [x] Per-coin and per-direction breakdowns partition the trades correctly (their trade counts sum to the totals) (tested).
- [x] The exported per-trade log contains one row per simulated trade with the required fields, and its wins/losses reconcile with the summary totals (tested).
- [x] The rendered report output contains the past-performance disclaimer (asserted, e.g. `"past performance" in output.lower()`) and exits 0 (tested).
- [x] Running the backtest twice on identical inputs produces identical summary metrics (determinism, tested).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 05-config-range-and-profiles

Finalize the backtest as a configurable, profile-aware command: a `backtest` config section (fees, slippage, risk-per-trade, optional max holding, date range, cache directory) with validation; `--from`/`--to` range flags; reuse of the same live configuration (watchlist, profile, weights, threshold, direction rule); and correct operation on both the futures and spot profiles.

### Implementation steps

- [x] Add a validated `backtest` configuration section (fee rate, slippage, risk-per-trade, optional maximum holding period, start/end dates, cache directory) with defaults and unknown-key rejection.
- [x] Add `--from`/`--to` flags to the `backtest` command and ensure only trades entered within the range are counted, with earlier candles used solely for indicator warm-up.
- [x] Ensure the backtest reuses the live scan configuration (watchlist, profile, weights, threshold, direction rule) and runs on both futures and spot profiles.
- [x] Add unit tests for config parsing/validation, date-range gating, and a spot-profile backtest.

### Acceptance criteria

- [x] The `backtest` config section parses with documented defaults; invalid values (negative fee, risk-per-trade out of range, start after end) raise a clear configuration error, and unknown keys are rejected (tested).
- [x] Only trades whose entry falls within the requested range are included; candles before the range are used only to warm up indicators (tested).
- [x] The backtest reuses the live configuration and produces trades under both the futures and spot profiles (tested via injected/mocked history).
- [x] Running `backtest --config <file> --from <date> --to <date>` exits 0 and its output contains the disclaimer plus the key metric labels (win rate, expectancy, profit factor, max drawdown) (tested).
- [x] No regression: the reused analysis modules (`indicators`, `structure`, `direction`, `scoring_model`, `trade_planner`) are not edited by this feature, and the existing `scan`/`market-data` tests remain unchanged and green (tested).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.
