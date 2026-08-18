# Tasks: TradingView Crypto Advisory Bot

Feature slug: `tradingview-crypto-advisor`
Requirements: `.sdd/tradingview-crypto-advisor/requirements.md`

Each task below is a vertical slice that cuts end-to-end (data fetch → scoring → ranking → CLI output) and grows the pipeline with real behavior. Tasks are numbered in implementation/dependency order.

---

## Task 01-walking-skeleton

Stand up the project and prove the whole pipe works end-to-end for a single coin: fetch one hardcoded symbol at one timeframe from TradingView through a mockable provider interface, pass its recommendation straight through, and print it as a rich table row from a typer CLI. This establishes the `AnalysisProvider` seam and the CLI entry point that every later task builds on.

### Implementation steps

- [x] Initialize a `uv` project with `pyproject.toml` targeting Python 3.11+; add runtime deps `tradingview-ta`, `PyYAML`, `typer`, `rich` and dev deps `pytest`, `ruff`, `mypy`.
- [x] Define `AnalysisProvider` as a Protocol/ABC with `get_analysis(symbol, exchange, screener, interval) -> TimeframeResult`, plus a `TimeframeResult` type carrying interval, recommendation label, and buy/neutral/sell counts.
- [x] Implement the concrete `tradingview-ta`-backed provider that wraps `TA_Handler` and maps the library response (and interval strings) into `TimeframeResult`.
- [x] Build a minimal typer CLI that fetches one hardcoded symbol (default Binance USDT pair, `crypto` screener) at one timeframe and prints a one-row `rich` table.
- [x] Add a unit test that feeds a canned `tradingview-ta`-shaped response to the concrete provider and asserts the mapped `TimeframeResult`.

### Acceptance criteria

- [x] `uv sync` installs all runtime and dev dependencies without error.
- [x] Running the CLI prints a `rich` table containing the symbol and its TradingView recommendation label, and the process exits with code 0.
- [x] `AnalysisProvider` is defined as a Protocol/ABC and the concrete implementation maps a canned `tradingview-ta` response into `TimeframeResult` (verified by the unit test).
- [x] The provider-mapping unit test passes, so `pytest` exits 0 (not the no-tests-collected exit code).

### Quality gates

- [x] `uv sync` succeeds.
- [x] `uv run python -m compileall src` reports no errors.
- [x] `uv run pytest` passes with exit code 0.

---

## Task 02-multi-timeframe-scoring

Extend the pipe from one timeframe to several: fetch a coin across multiple configured timeframes and combine them into a single weighted score via a pure `ScoringEngine`, with longer timeframes weighted more heavily. The CLI shows the weighted score together with the per-timeframe breakdown so the reasoning is transparent.

### Implementation steps

- [x] Implement a pure `ScoringEngine.score(symbol, results, weights)` that maps labels `STRONG_BUY=+2, BUY=+1, NEUTRAL=0, SELL=-1, STRONG_SELL=-2`, multiplies each by its per-timeframe weight, and sums to a single weighted score.
- [x] Retain the per-timeframe breakdown on the returned `CoinScore` for display.
- [x] Have the CLI fetch the coin across a built-in default set of timeframes (`15m, 1h, 4h, 1d`) with default weights (longer > shorter) and render the score plus breakdown.
- [x] Add unit tests covering pinned score values, the all-neutral case, and a case where longer-timeframe weighting flips the outcome.

### Acceptance criteria

- [x] For inputs `15m=STRONG_BUY` (weight 1) and `4h=STRONG_SELL` (weight 3), `ScoringEngine` returns score `-4` (tested).
- [x] All-neutral timeframes produce a score of exactly `0` (tested).
- [x] A mixed case where equal weights would give a positive score but the configured longer-timeframe weights yield a negative score is verified (tested), proving longer timeframes dominate.
- [x] The CLI output shows both the weighted score and the per-timeframe recommendation breakdown for the coin.

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 03-watchlist-ranking-and-signals

Grow from one coin to the full watchlist: introduce the `Runner` that orchestrates the watchlist × timeframes sequentially with a configurable inter-request delay (via an injectable sleep), score every coin, then rank them descending and tag each `BUY` / `SELL` / `NEUTRAL` against configurable thresholds. The CLI renders the ranked table with signals highlighted (▲ green buy, ▼ red sell). The analysis core remains importable without the CLI.

### Implementation steps

- [x] Implement the `Runner` that iterates a built-in default watchlist across the configured timeframes sequentially, sleeping a configurable delay between requests through an injectable sleep function, and feeds results to `ScoringEngine`.
- [x] Implement a pure `SignalClassifier`/`Ranker.rank(scores, buy_threshold, sell_threshold)` that sorts descending by score and tags each coin `BUY` (≥ buy threshold), `SELL` (≤ sell threshold), or `NEUTRAL` (strictly between).
- [x] Render the ranked list as a `rich` table with buy rows highlighted green/▲ and sell rows red/▼.
- [x] Keep all analysis modules (Runner, ScoringEngine, Ranker, provider interface) in a core package that does not import `typer` or `rich`.
- [x] Add unit tests for ordering, threshold tagging at exact boundaries, sequential throttling via a mock provider + mock sleep, and a core-import test.

### Acceptance criteria

- [x] Multiple coins are printed sorted from strongest buy at the top to strongest sell at the bottom (ordering tested).
- [x] A coin scoring exactly at the buy threshold is tagged `BUY`, one exactly at the sell threshold is tagged `SELL`, and one strictly between is tagged `NEUTRAL` (all tested at the boundary values).
- [x] The `Runner` issues exactly one provider call per (symbol, timeframe) in sequence, and the injectable sleep is called with the configured delay between requests (verified via mock provider + mock sleep, not wall-clock timing).
- [x] Importing the core analysis package succeeds without importing `typer` or `rich` (verified by an import test that fails if the CLI modules are pulled in).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 04-yaml-configuration

Replace the built-in defaults with a real YAML configuration layer, threaded end-to-end. A `Config` module loads and validates YAML into a typed config (watchlist, exchange, screener, timeframes, weights, thresholds, request delay, watch interval), applies defaults for omitted fields, and raises clear errors on invalid input. The CLI gains a `--config` flag and still runs with no file using built-in defaults.

### Implementation steps

- [x] Implement `load_config(path)` producing a typed `Config`, with documented defaults applied for any omitted field and validation that raises a clear, typed configuration error on malformed or out-of-range input.
- [x] Add a `--config PATH` CLI option; when omitted, fall back to built-in defaults.
- [x] Thread `exchange` and `screener` from config through to every provider call.
- [x] Add unit tests for parsing, defaulting, validation errors, and an end-to-end test proving config values change output.

### Acceptance criteria

- [x] A valid YAML file parses into the expected typed `Config` (tested).
- [x] Omitted fields receive their documented defaults (tested).
- [x] Malformed YAML or an out-of-range/invalid value raises a clear configuration error rather than producing a result (tested).
- [x] A watchlist and thresholds defined only in a YAML file change the printed ranking compared to the built-in defaults (end-to-end tested).
- [x] The `exchange` and `screener` from config are passed through to each provider call (asserted via a mock provider).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 05-resilience-graceful-degradation

Make a run survive bad symbols. The `Runner` catches per-symbol fetch failures, skips the offending coin, records the reason, and continues; a failure summary is printed at the end and a single bad symbol never aborts the whole run. Verified with a mock provider that fails selectively.

### Implementation steps

- [x] Wrap each symbol's fetch so an exception is caught, recorded as a failure (symbol + reason), and does not stop the run.
- [x] Define and implement the rule that a symbol failing any required timeframe is recorded as failed and excluded from the ranking.
- [x] Print a failure summary listing each failed symbol and its reason after the ranking.
- [x] Add unit tests using a mock provider that raises for selected symbols.

### Acceptance criteria

- [x] With a mock provider that raises for one symbol, the ranking is still produced for all remaining coins and the failed symbol appears in the failure summary (tested).
- [x] A symbol whose fetch fails on one timeframe is excluded from the ranking and reported as failed, per the defined rule (tested).
- [x] The failure summary shows each failed symbol together with its failure reason.
- [x] The process exits with code 0 when a partial ranking is produced despite failures.

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 06-cli-output-options

Round out the interface with the remaining flags: `--only-signals` to collapse to the actionable shortlist, `--json PATH` to write a structured record derived from the same result as the table, and `--watch INTERVAL` to re-run on a timer via the injectable sleep.

### Implementation steps

- [x] Add `--only-signals` to omit `NEUTRAL` coins from the rendered output.
- [x] Add `--json PATH` to serialize the run result (scores + signals + per-timeframe breakdown) to JSON, derived from the same in-memory result used for the table.
- [x] Add `--watch INTERVAL` to loop the one-shot run on a timer using the injectable sleep.
- [x] Add unit tests for filtering, JSON/table parity, and the watch loop via a mocked sleep and run counter.

### Acceptance criteria

- [x] With `--only-signals`, `NEUTRAL` coins are omitted and only threshold-crossing coins are shown (tested).
- [x] `--json PATH` writes valid JSON, and reloading it yields the same scores and signals as the table output — proving a single source of truth (tested).
- [x] With `--watch` and a mocked sleep + run counter, N iterations produce N runs and the sleep is called with the configured interval (tested, not wall-clock).
- [x] `--only-signals`, `--json`, and `--watch` all appear in the CLI `--help` output.

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.
