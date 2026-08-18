# Tasks: Multi-Factor Scoring Engine & Trade Levels

Feature slug: `multi-factor-scoring-engine`
Requirements: `.sdd/multi-factor-scoring-engine/requirements.md`

Each task is a vertical slice that cuts end-to-end (data fetch → compute → direction/score → CLI output) and grows the engine with real behavior. Tasks are numbered in implementation/dependency order. New configuration settings are added by the task that first needs them (part of that slice), not as a separate horizontal layer. Through tasks 01–06 the new settings use in-code defaults on the existing `Config`; task 07 finalizes the full YAML surface and retires the v1 thresholds.

Quality gates are identical across tasks and deterministic: the test suite, linter, type checker, and byte-compile must all pass.

---

## Task 01-market-data-seam

Establish the Binance market-data layer as a mockable seam and prove it end-to-end: for one coin, fetch OHLCV candles and an order-book snapshot from Binance and print the latest close and bid/ask spread from the CLI. This mirrors the v1 `AnalysisProvider` pattern and gives every later task its data foundation.

### Implementation steps

- [x] Add runtime deps `ccxt`, `pandas`, `ta` and dev dep `pandas-stubs` to `pyproject.toml`; add `[[tool.mypy.overrides]]` with `ignore_missing_imports = true` for `ccxt` and `ta` (both ship no type information) so `mypy --strict` passes.
- [x] Define `MarketDataProvider` as a Protocol with `get_ohlcv(symbol, timeframe, limit) -> Candles` and `get_order_book(symbol, depth) -> OrderBook`, plus `Candles` (wraps a pandas OHLCV frame) and `OrderBook` (best bid/ask + cumulative depth) types.
- [x] Implement `CcxtBinanceProvider` mapping `ccxt` OHLCV and order-book responses into `Candles`/`OrderBook`, importing `ccxt` lazily.
- [x] Add a CLI path that fetches one coin's candles + order book and prints latest close and spread.
- [x] Add unit tests mapping canned `ccxt`-shaped OHLCV and order-book responses into `Candles`/`OrderBook`; extend `tests/test_core_independence.py` to import the new core module with `typer`/`rich` blocked.

### Acceptance criteria

- [x] `uv sync` installs `ccxt`, `pandas`, `ta`, and `pandas-stubs`.
- [x] `MarketDataProvider` is a Protocol and `CcxtBinanceProvider` maps a canned `ccxt` OHLCV response into a `Candles` frame with the expected number of rows and columns (tested).
- [x] A canned order-book response maps to an `OrderBook` whose spread equals best-ask minus best-bid (tested).
- [x] Running the CLI against one coin prints a numeric latest close and a numeric spread, exit code 0.
- [x] Importing the new market-data module succeeds with `typer` and `rich` blocked (core-independence test passes).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 02-indicator-features

Turn raw candles into computed technical features. For one coin and timeframe, compute EMA 20/50/200, RSI, MACD, ROC, ATR, OBV slope, Bollinger width, and relative volume, and surface a few of them through the CLI so the computation is observable end-to-end.

### Implementation steps

- [x] Implement `compute_features(candles) -> TimeframeFeatures` using `ta` + pandas, covering EMA 20/50/200, RSI, MACD (line/signal/hist), ROC, ATR, OBV (value + slope), Bollinger width, and relative volume (current vs rolling average).
- [x] Have the CLI compute and display a representative subset (e.g. EMA 20/50/200 and RSI) for the fetched coin.
- [x] Add unit tests feeding hand-built OHLCV frames with known outcomes; extend `tests/test_core_independence.py` to cover `indicators`.

### Acceptance criteria

- [x] For a strictly rising synthetic price series, `compute_features` returns EMA20 > EMA50 > EMA200 and RSI above 50 (tested).
- [x] For a strictly falling series, EMA20 < EMA50 < EMA200 and RSI below 50 (tested).
- [x] ATR is positive and relative volume equals current volume divided by the rolling average on a known frame (tested).
- [x] OBV slope is positive when price and volume rise together (tested).
- [x] The CLI prints the computed EMA values and RSI for the coin, exit code 0.
- [x] Importing `indicators` succeeds with `typer` and `rich` blocked (core-independence test passes).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 03-direction-and-orchestration

Decide a trade direction per coin and orchestrate the whole watchlist. Add market-structure detection and the direction rule (higher timeframes must agree; conflict → none; BTC can veto), fetch BTC context once per run, and have the Runner walk the watchlist across timeframes through both data sources with per-source throttling and graceful skipping. The CLI shows each coin with its direction (long / short / none).

### Implementation steps

- [x] Implement `structure.analyze(candles) -> StructureState` returning BULLISH / BEARISH / BROKEN from swing highs/lows, and exposing the detected swing highs/lows as support/resistance pivot levels (consumed later by the trade planner) so pivots come from our own OHLCV rather than depending on TradingView.
- [x] Implement `direction.decide(features_by_tf, structure_by_tf, btc_context) -> Direction` (LONG/SHORT/NONE): 4h and 1d must agree on EMA stack + structure, conflict → NONE, BTC context vetoes an opposing direction.
- [x] Extend the Runner to iterate the watchlist × timeframes across the market-data and TradingView providers, compute features + structure per timeframe, fetch BTC context once, and decide direction per coin.
- [x] Add per-source request delays (separate TradingView and Binance delays) using the injectable-sleep pattern; skip and record a coin whose market data cannot be fetched at all.
- [x] Render each coin with its direction in the CLI, and print a failure summary for skipped coins.
- [x] Add unit tests for structure detection, the exposed swing pivot levels, the direction rule (agreement, conflict → none, BTC veto), and Runner orchestration/skip behavior with mocked providers; extend `tests/test_core_independence.py` to cover `structure` and `direction`.

### Acceptance criteria

- [x] Synthetic higher-high/higher-low candles yield BULLISH and lower-high/lower-low candles yield BEARISH, and the returned pivot levels match the synthetic swing highs/lows (tested).
- [x] When 4h and 1d agree bullish, direction is LONG; when they disagree, direction is NONE (both tested).
- [x] A bearish BTC context vetoes an otherwise-LONG coin to NONE (tested).
- [x] With mocked providers, the Runner produces a direction for each successfully-fetched coin and the per-source sleeps are called with the configured TradingView and Binance delays (tested, mocked sleep).
- [x] A coin whose market data fetch raises is skipped, recorded as a failure, and the run still processes the rest (tested); the CLI shows directions plus a failure summary, exit code 0.
- [x] Importing `structure` and `direction` succeeds with `typer` and `rich` blocked (core-independence test passes).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 04-scoring-core-categories

Introduce the 0–100 scoring model with fractional per-category credit and weights, implementing the categories that depend only on features, structure, and BTC context: Trend alignment (25), Market structure (20), Momentum (15), and BTC alignment (5). Fold TradingView's recommendation into Trend/Momentum as a best-effort contribution that degrades when rate-limited. Rank coins by total and surface only those with a direction and a total at or above the quality threshold. The CLI shows the ranked short list with direction and score/100.

### Implementation steps

- [x] Implement `ScoringModel.score(...) -> QualityScore` with fractional (0–1) directional credit per category × weight, producing a 0–100 total plus a per-category breakdown; add `quality_threshold` and `category_weights` to config defaults.
- [x] Implement the Trend, Market-structure, Momentum, and BTC-alignment categories; blend the TradingView recommendation into Trend/Momentum, contributing nothing when the TradingView fetch failed.
- [x] Rank coins by total descending and filter to those with direction ≠ NONE and total ≥ threshold; render the ranked short list with direction and score/100.
- [x] Add unit tests for category fractions, weight sensitivity, threshold/direction gating, and TradingView-degraded scoring; extend `tests/test_core_independence.py` to cover `scoring_model`.

### Acceptance criteria

- [x] A coin fully confirming its direction across trend, structure, momentum, and BTC scores ≥ 90% of the implemented categories' maximum (≥ 58.5 of the 65 available points); a fully contradicting coin scores ≤ 5 points (both tested with pinned inputs).
- [x] Increasing a category's weight increases the total for a coin that earns credit in that category (tested).
- [x] A coin with direction NONE, or a total below the threshold, is excluded from the surfaced list (tested).
- [x] When the TradingView contribution is absent (simulated rate limit), the coin is still scored from the remaining factors and still surfaced, with a lower Trend/Momentum contribution (tested).
- [x] The CLI default view lists only surfaced coins, ranked by score, each showing direction and score/100, exit code 0.
- [x] Importing `scoring_model` succeeds with `typer` and `rich` blocked (core-independence test passes).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 05-volume-breakout-liquidity-categories

Complete the market-data-driven half of the model: add Volume confirmation (15) from relative volume and OBV, Breakout/pullback quality (10) from price action against structure/pivots, and Liquidity (5) from order-book depth and spread. These categories now contribute to the same 0–100 total and change which coins clear the threshold.

### Implementation steps

- [x] Implement the Volume-confirmation category (relative volume above a configurable multiple, OBV trending with direction).
- [x] Implement the Breakout/pullback-quality category (clean break of a recent swing/pivot, or a healthy pullback to support/EMA that holds).
- [x] Implement the Liquidity category from the order-book snapshot (cumulative depth ≥ configurable minimum and spread ≤ configurable maximum); add the relevant config defaults.
- [x] Wire all three into `ScoringModel` so the total reflects them.
- [x] Add unit tests for each of the three categories.

### Acceptance criteria

- [x] High relative volume with OBV rising in the trade direction earns Volume credit fraction ≥ 0.9; flat volume earns ≤ 0.1 (tested).
- [x] A clean breakout of a recent swing earns higher Breakout/pullback credit than an extended, mid-range move (tested).
- [x] Order-book depth below the minimum or spread above the maximum earns Liquidity credit fraction ≤ 0.1; ample depth with a tight spread earns ≥ 0.9 (tested).
- [x] A coin's total score changes when these categories are added versus the core-only total for the same inputs (tested).
- [x] Running the CLI over a watchlist reflects these categories in the rendered score/100, and a coin's surfaced/short-list status can change once they are wired (observed end-to-end, exit code 0).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 06-trade-planner

Compute a concrete trade plan for each surfaced setup and close the scoring model with the Risk-to-reward category (5). From the direction, structure, pivots, and ATR, derive entry, stop-loss (invalidation + ATR buffer), take-profit (next structural level, floored by a target risk-to-reward), and the resulting risk-to-reward ratio. The CLI shows entry / SL / TP / R:R columns for each surfaced setup, and the total is now the full 100 points.

### Implementation steps

- [x] Implement `TradePlanner.plan(direction, candles, pivots, atr) -> TradePlan` (entry = latest close; stop = invalidation swing level ± `atr_buffer`×ATR; take-profit = next structural/pivot level floored by `target_rr`; compute risk_reward); add `atr_buffer`, `target_rr`, and `reference_timeframe` config defaults.
- [x] Implement the Risk-to-reward scoring category from the plan's ratio (full credit at ≥ target, scaled below) and wire it into `ScoringModel` for a full 100-point total.
- [x] Render entry, stop-loss, take-profit, and risk-to-reward columns for each surfaced setup in the CLI.
- [x] Add unit tests for the trade plan and the R:R category; extend `tests/test_core_independence.py` to cover `trade_planner`.

### Acceptance criteria

- [x] For a known long structure and ATR, the stop-loss is below the invalidation swing low by the configured ATR buffer (tested); for a short, it is above the invalidation swing high (tested).
- [x] The take-profit yields a risk-to-reward at or above the configured target, and the computed risk_reward matches (take-profit − entry) / (entry − stop) for a long (tested).
- [x] The Risk-to-reward category earns full credit at or above the target ratio and reduced credit below it (tested).
- [x] The maximum achievable total across all eight categories is 100 for a fully-confirming setup (tested).
- [x] The CLI shows entry, SL, TP, and R:R for each surfaced setup, exit code 0.
- [x] Importing `trade_planner` succeeds with `typer` and `rich` blocked (core-independence test passes).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 07-config-and-output-finalize

Finalize the configuration surface and output controls. Move every new setting into validated YAML, retire the v1 buy/sell thresholds, add the long-only toggle, and add the CLI output controls: `--details` (expand the category breakdown), `--all` (include sub-threshold and no-direction coins), and full structured JSON. The default view remains the threshold-filtered ranked short list.

### Implementation steps

- [x] Extend `load_config` to parse and validate all new fields (category weights, quality threshold, per-source delays, OHLCV lookback, order-book depth/spread cutoffs, indicator params, ATR buffer, target R:R, reference timeframe, long-only) with defaults; remove `buy_threshold`/`sell_threshold` and reject them as unknown keys.
- [x] Apply the long-only toggle so short setups are excluded when enabled.
- [x] Add the `--details` and `--all` CLI flags and make the threshold-filtered short list the default view.
- [x] Write a full structured JSON record (direction, total, category breakdown, trade plan, failures) derived from the same in-memory result as the table.
- [x] Confirm `--config` loads the YAML surface and `--watch` re-runs the new pipeline on an interval (retained from v1 over the rebuilt engine).
- [x] Add unit tests for the new config validation, retired-key rejection, long-only filtering, JSON/table parity, and the watch loop.

### Acceptance criteria

- [x] A YAML file setting weights, threshold, and other new fields parses into the expected config; omitted fields take documented defaults (tested).
- [x] Invalid values (weight negative, threshold outside 0–100, target R:R ≤ 0) raise a clear configuration error, and a file containing `buy_threshold` or `sell_threshold` is rejected as an unknown key (tested).
- [x] With long-only enabled, no short setup is surfaced (tested).
- [x] `--all` includes sub-threshold and no-direction coins while the default view excludes them (tested).
- [x] `--details` output includes the individual category scores for a surfaced setup (tested).
- [x] The JSON record reloads to the same directions, totals, and trade-plan values shown in the table (tested).
- [x] With a mocked sleep and a run counter, `--watch` runs the new pipeline N times and sleeps with the configured interval (tested, not wall-clock); `--config` applies a YAML file's settings to the run (tested).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.
