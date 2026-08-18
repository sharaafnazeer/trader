# Requirements: Multi-Factor Scoring Engine & Trade Levels

> Evolves the existing `tradingview-crypto-advisor` feature (v1). v1 is a TradingView-recommendation
> screener that ranks a watchlist and highlights buy/sell signals. This phase (v2) turns it into a
> multi-factor scoring engine that emits fewer, higher-conviction directional setups, each with a
> concrete entry, stop-loss, take-profit and risk-to-reward. It reuses the v1 analysis-core/CLI
> separation and the mockable-provider pattern, and modifies several v1 modules.

## Problem Statement

As a crypto trader, the v1 tool tells me *which coin and which direction* looks favorable, but it does not tell me how good the setup actually is or how to trade it. A single TradingView "Buy/Sell" gauge is shallow — it ignores whether the trend, market structure, momentum, and volume all agree, whether the wider market (BTC) supports the move, whether the coin is liquid enough to trade, and whether the risk-to-reward is even worth taking. Worst of all, it surfaces a signal for every coin every time, so I drown in low-quality alerts and can't tell a genuine high-conviction setup from noise. And even when I find a good coin, I still have to work out my entry, stop-loss and target by hand.

## Solution

An upgraded advisory engine that scores each coin out of 100 across many weighted categories — trend alignment, market structure, momentum, volume confirmation, breakout/pullback quality, BTC market alignment, liquidity, and risk-to-reward — after first deciding a clear trade direction (long, short, or none) from the higher-timeframe trend and structure, with BTC able to veto a trade. It only surfaces a coin as a signal when there is a clear direction *and* the score clears a configurable quality threshold (default 75/100), so I see a short list of high-conviction setups instead of constant noise. For every surfaced setup it also computes a concrete trade plan — entry, stop-loss (the invalidation level), take-profit, and the resulting risk-to-reward — so I know not just *what* to trade but *how*. It still places no trades: a human stays in control. The data comes from a combination of TradingView's ratings and live Binance market data (candles and order book), with the analysis computed by the tool itself.

## User Stories

1. As a crypto trader, I want each coin scored out of 100 across multiple weighted categories, so that I get a single conviction number instead of a shallow buy/sell label.
2. As a crypto trader, I want the bot to decide a clear trade direction (long, short, or none) before scoring, so that the score reflects conviction in an actual tradeable direction.
3. As a crypto trader, I want direction decided from the higher timeframes (4h and daily) trend and structure, so that I trade with the dominant trend rather than short-term noise.
4. As a crypto trader, I want the bot to return "no trade" when the timeframes or signals disagree, so that I am not pushed into ambiguous setups.
5. As a crypto trader, I want BTC's direction to be able to veto a trade, so that I don't take a long in an altcoin while the whole market is falling.
6. As a crypto trader, I want trend alignment scored from EMA 20/50/200 stacking across timeframes, so that I know price is structurally in a trend.
7. As a crypto trader, I want market structure (higher highs/lows or lower highs/lows) scored, so that I know the trend is actually intact.
8. As a crypto trader, I want momentum scored from RSI, MACD and rate-of-change, so that I know the move has energy behind it.
9. As a crypto trader, I want volume confirmation scored from relative volume and on-balance-volume, so that I can tell a real move from a low-conviction drift.
10. As a crypto trader, I want breakout or pullback quality scored, so that I favor clean entries over chasing extended moves.
11. As a crypto trader, I want BTC market alignment scored as its own factor, so that setups agreeing with the broader market rank higher.
12. As a crypto trader, I want liquidity scored from order-book depth and spread, so that I avoid coins I cannot enter or exit cleanly.
13. As a crypto trader, I want risk-to-reward scored as a factor, so that setups with poor reward for their risk are penalized.
14. As a crypto trader, I want each category to give partial credit rather than all-or-nothing, so that the score is smooth and fair.
15. As a crypto trader, I want the category weights configurable, so that I can tune the model to my own priorities.
16. As a crypto trader, I want a configurable quality threshold (default 75), so that I control how selective the bot is.
17. As a crypto trader, I want only setups that clear the threshold shown by default, so that I get a high-conviction short list instead of constant alerts.
18. As a crypto trader, I want a concrete entry price for each surfaced setup, so that I know where to get in.
19. As a crypto trader, I want a stop-loss placed at the trade's invalidation level (with a volatility buffer), so that I'm stopped out only when the setup is genuinely wrong.
20. As a crypto trader, I want a take-profit derived from the next structural resistance and floored by a target risk-to-reward, so that my target is realistic and worthwhile.
21. As a crypto trader, I want the resulting risk-to-reward ratio displayed, so that I can judge the trade at a glance.
22. As a crypto trader, I want both long and short setups by default with an option to restrict to longs only, so that I can match how I actually trade.
23. As a crypto trader, I want the analysis computed from live Binance candles and order book plus TradingView ratings, so that the richer factors are based on real market data.
24. As a crypto trader, I want the tool to keep working when TradingView is rate-limited, scoring the coin on the remaining factors, so that a rate limit never blanks out my scan.
25. As a crypto trader, I want a coin that cannot be analyzed to be skipped and reported, so that one bad symbol never aborts the run.
26. As a crypto trader, I want requests spaced out per data source, so that the tool stays reliable against both TradingView and Binance.
27. As a crypto trader, I want to see just the ranked short list by default, so that I can act quickly.
28. As a crypto trader, I want an option to expand the full category breakdown per coin, so that I can understand why a setup scored the way it did.
29. As a crypto trader, I want an option to show all coins including those below the threshold, so that I can see the whole picture when I want it.
30. As a crypto trader, I want the full results written as structured JSON, so that I can feed setups into other tools or keep a record.
31. As a crypto trader, I want sensible defaults out of the box (Binance USDT pairs, default weights and threshold), so that I get value before configuring anything.
32. As a crypto trader, I want a watch mode that re-runs on an interval, so that I can monitor for new high-conviction setups without re-typing the command.
33. As a crypto trader, I want the tool to remain advisory with no trade execution, so that I stay in control.

## User Acceptance Tests

1. Given a coin whose higher timeframes (4h and daily) show an upward-stacked EMA structure and higher highs/lows, when it is analyzed, then its direction is reported as long.
2. Given a coin whose 4h and daily trends disagree, when it is analyzed, then its direction is reported as none and it is not surfaced as a signal regardless of its category scores.
3. Given BTC is in a strong downtrend, when an altcoin that would otherwise look bullish is analyzed, then a long signal for that altcoin is vetoed.
4. Given a coin with strong agreement across trend, structure, momentum and volume in a clear direction, when it is scored, then its total is high (near the top of the 0–100 range).
5. Given a coin whose score is below the configured quality threshold, when the default view is shown, then that coin is not listed as a signal.
6. Given a coin whose score is at or above the threshold with a clear direction, when the default view is shown, then it appears in the ranked short list with its direction, score, entry, stop-loss, take-profit and risk-to-reward.
7. Given a surfaced long setup, when its trade plan is shown, then the stop-loss sits below the trend's invalidation level plus a volatility buffer, and the take-profit sits at or beyond a resistance level that yields at least the configured target risk-to-reward.
8. Given the category weights are changed in configuration, when the same coin is analyzed, then its total score changes accordingly.
9. Given the quality threshold is raised, when the watchlist is analyzed, then fewer or equal coins are surfaced as signals.
10. Given the long-only option is enabled, when a coin's direction is short, then it is not surfaced as a signal.
11. Given TradingView is rate-limited for a coin, when that coin is analyzed, then it is still scored from the Binance-derived factors and still appears (without the TradingView contribution) rather than failing.
12. Given a coin whose market data cannot be fetched at all, when the watchlist is analyzed, then that coin is skipped and listed in a failure summary while the rest are still analyzed.
13. Given the "show all" option, when the watchlist is analyzed, then coins below the threshold and those with no direction are also displayed.
14. Given the "details" option, when a setup is shown, then the individual category scores that make up its total are displayed.
15. Given the JSON output option, when the watchlist is analyzed, then a structured record containing every coin's direction, total score, category breakdown, and trade plan is written, and it reflects the same values shown on screen.
16. Given no configuration file, when the watchlist is analyzed, then the tool runs on built-in defaults (Binance USDT pairs, default weights, threshold 75).
17. Given watch mode with an interval, when the bot is started, then it re-runs the analysis at that interval until stopped.
18. Given a watchlist of several coins, when a run executes, then requests to each data source are spaced out by that source's configured delay.

## Definition of Done

- All user acceptance tests pass.
- Each coin is scored 0–100 across the eight weighted categories, after a direction (long/short/none) is determined, and the category breakdown is available.
- A signal is surfaced only when direction is not "none" and the total meets or exceeds the configured quality threshold.
- Every surfaced setup includes a trade plan: entry, stop-loss (invalidation + volatility buffer), take-profit (structure-based, floored by target risk-to-reward), and the resulting risk-to-reward ratio.
- Direction is driven by higher-timeframe trend and structure, resolves to "none" on conflict, and BTC can veto a trade.
- Category weights, quality threshold, per-source request delays, candle lookback, order-book cutoffs, indicator parameters, volatility buffer, target risk-to-reward, reference timeframe, and long-only toggle are all configurable, with sensible defaults.
- Live Binance candles and order book are used for the computed factors; TradingView ratings are blended in as a best-effort contribution that degrades gracefully when rate-limited.
- A coin that cannot be analyzed is skipped and reported; one bad symbol never aborts the run.
- The default view is the threshold-filtered ranked short list; options exist to expand the category breakdown, to show all coins, and to write full JSON.
- The old v1 buy/sell score thresholds are retired in favor of the single quality threshold.
- Automated tests exist and pass for the indicator computation, market-structure detection, direction logic, scoring model, and trade-planner modules, plus configuration validation and an orchestration test using mocked data providers.
- The analysis core remains importable independently of the CLI.
- The tool performs no trade execution.
- Project sets up and runs cleanly with the chosen tooling (`uv sync`).

## Out of Scope

- Trade execution, order management, and exchange API keys (still reserved for a future execution phase).
- Push notifications or alerting channels (Telegram, webhooks, email); this phase surfaces signals via the CLI and watch mode only.
- Persisting signal history, hit-rate tracking, threshold calibration, and backtesting.
- Broader market-context inputs beyond BTC (total market cap, BTC dominance, Fear & Greed index).
- Concurrent/parallel fetching; this phase remains sequential with per-source throttling.
- Dynamic watchlist discovery (top-N by market cap/volume); the watchlist stays fixed and user-defined.
- Any interface other than the CLI (web dashboard, mobile).

## Further Notes

- This phase reverses a v1 decision: v1 deliberately did not compute its own indicators and relied solely on TradingView's aggregated recommendation. v2 adds a Binance/CCXT market-data layer and computes the multi-factor model itself, because roughly half the scoring categories (market structure, volume dynamics, breakout quality, liquidity, ATR-based risk) cannot be derived from TradingView's snapshot.
- Binance spot public market data (OHLCV and order book) requires no API key via CCXT, so the tool remains key-free this phase.
- The scores and trade plans are heuristic, mechanical advice — not guarantees. The same advisory disclaimer as v1 applies.
- TradingView's contribution is intentionally lightweight and best-effort: because it rate-limits aggressively, the model must stand on the Binance-derived factors alone when TradingView is unavailable.

---

## Technical Annex
> Written against codebase as of: 2026-07-28

This section captures the architectural and automated-testing decisions from the planning session for architect/developer review. When this document is used to generate tasks, each decision below will be verified against the current state of the codebase and any conflicts flagged before proceeding.

### Current state (baseline)

The v1 `tradingview-crypto-advisor` feature is fully implemented under `src/trader/`:
- `provider.py` — `AnalysisProvider` Protocol + `TradingViewProvider` (wraps `tradingview-ta`), `TimeframeResult`, `map_summary`.
- `scoring.py` — `ScoringEngine.score(...)` producing a weighted multi-timeframe `CoinScore` from recommendation labels.
- `ranking.py` — pure `rank(...)`, `Signal` (`StrEnum`), `RankedCoin`, `Ranking`.
- `runner.py` — `Runner` (injectable sleep), `RunResult`, `Failure`, sequential throttled orchestration with graceful per-symbol degradation.
- `config.py` — `load_config`, frozen `Config`, `ConfigError`, validation + defaults.
- `cli.py` — `typer` app + `rich` rendering; `--config`, `--only-signals`, `--json`, `--watch`. Only place `typer`/`rich` are imported.

Tooling: Python ≥3.11, `uv` + `pyproject.toml`; deps `tradingview-ta`, `PyYAML`, `typer`, `rich`; dev `pytest`, `ruff`, `mypy` (strict). The analysis core is enforced import-independent of the CLI by `tests/test_core_independence.py`.

### Architectural Decisions

**New dependencies**
- `ccxt` — Binance spot public market data (OHLCV + order book), key-free.
- `pandas` — candle frames and the bespoke structure/volume logic.
- `ta` (pure-Python, pandas-based) — EMA/RSI/MACD/ATR/OBV/Bollinger. Chosen over TA-Lib to avoid a C toolchain dependency and keep `uv sync` self-contained.
- Dev: `pandas-stubs` (for `mypy --strict`).

**New data seam — `MarketDataProvider`** (Protocol + concrete CCXT impl), mirroring the v1 `AnalysisProvider` pattern so it is mockable in tests:
- `get_ohlcv(symbol, timeframe, limit) -> Candles` where `Candles` wraps a `pandas.DataFrame` of OHLCV (default `limit=300`, configurable).
- `get_order_book(symbol, depth) -> OrderBook` exposing best bid/ask (spread) and cumulative depth within a configurable band.
- Concrete `CcxtBinanceProvider` maps `ccxt` responses into these types; a thin unit test verifies mapping against a canned `ccxt`-shaped response (no network). Imported lazily like `TradingViewProvider`.

**New pure modules** (no I/O, deterministic, no `typer`/`rich`):
- `indicators.py` — `compute_features(candles: Candles) -> TimeframeFeatures` returning EMA 20/50/200, RSI, MACD (line/signal/hist), ROC, ATR, OBV series/slope, Bollinger width, relative volume (current vs rolling average). Uses `ta` + pandas.
- `structure.py` — `analyze(candles: Candles) -> StructureState` detecting swing highs/lows → `BULLISH` (higher highs & higher lows) / `BEARISH` (lower highs & lows) / `BROKEN`. Custom pandas logic.
- `direction.py` — `decide(features_by_tf, structure_by_tf, btc_context, cfg) -> Direction` (`LONG`/`SHORT`/`NONE`). Rule: 4h + 1d must agree on EMA-stack + structure; conflict → `NONE`; BTC context vetoes an opposing direction. Fast timeframes are not used for direction.
- `scoring_model.py` — `ScoringModel.score(...) -> QualityScore` implementing the 8-category rubric. Each category yields a fraction in `[0,1]` (directional partial credit), multiplied by its weight; total is `0..100`. `QualityScore` carries the per-category breakdown. Categories and default weights: Trend alignment 25, Market structure 20, Momentum 15, Volume confirmation 15, Breakout/pullback quality 10, BTC alignment 5, Liquidity 5, Risk-to-reward 5. The v1 TradingView recommendation is folded in as an input to the Trend/Momentum categories (best-effort; absent contribution when rate-limited).
- `trade_planner.py` — `TradePlanner.plan(direction, candles, pivots, atr, cfg) -> TradePlan`. Entry = latest close. Stop = structural invalidation (last higher-low for long / lower-high for short) padded by `atr_buffer * ATR`. Take-profit = next structural/pivot level, floored by `target_rr`; if nearest level < target R:R, extend or mark the R:R category low. `TradePlan` carries entry, stop, take_profit, risk_reward, invalidation. Reference timeframe configurable (default `4h`).

**New per-run context — `MarketContext`**: computed once per run (BTC regime from BTC's own features/structure), injected into `direction` and `scoring_model`.

**Modified modules**
- `provider.py` (`AnalysisProvider`) — extended to expose pivot points and treated as best-effort; a rate-limit/error contributes "no TradingView agreement" rather than failing the coin.
- `runner.py` (`Runner`) — orchestrates the richer per-coin pipeline: fetch OHLCV per timeframe + order book (Binance) + TradingView rating (best-effort) → `compute_features` → `structure.analyze` → `direction.decide` → `scoring_model.score` → `trade_planner.plan`. BTC context fetched once per run. **Per-source throttling** via separate configurable delays (TradingView delay ≫ Binance delay), keeping the injectable-sleep pattern. Graceful degradation retained: total market-data failure for a symbol records a `Failure` and skips it; TradingView-only failure degrades the score. `RunResult` now holds ranked `Setup` objects + failures.
- `config.py` (`Config`) — add: `quality_threshold` (default 75), `category_weights` (mapping with the defaults above), per-source delays (`tradingview_delay`, `binance_delay`), `ohlcv_lookback` (default 300), order-book `depth_band`/`min_depth`/`max_spread`, indicator params (RSI bounds, `relative_volume_multiple`), `atr_buffer`, `target_rr` (default 2.0), `reference_timeframe` (default `4h`), `long_only` (default false). **Remove** `buy_threshold`/`sell_threshold`. Validation: weights non-negative, threshold in `[0,100]`, `target_rr > 0`, etc.
- `ranking.py` / signal — rank by `QualityScore.total` descending; a `Setup` is emitted only when `direction != NONE and total >= quality_threshold` (and not filtered by `long_only`). `Signal`/`RankedCoin` evolve into a `Setup` object carrying symbol, direction, score, breakdown, and trade plan.
- `cli.py` — default renders the threshold-filtered ranked short list with columns `Symbol · Direction (▲long/▼short) · Score/100 · Entry · SL · TP · R:R`. New flags: `--details` (expand category breakdown), `--all` (include sub-threshold and no-direction coins). `--only-signals` behavior becomes the default; `--json` writes the complete record (direction, total, breakdown, trade plan, failures) derived from the same in-memory result. `--watch`/`--config` retained.

**Data flow (per coin):** Binance OHLCV ×timeframes + order book → `compute_features` per timeframe + `structure.analyze` per timeframe; TradingView rating (best-effort) → blended; `direction.decide` (HTF + BTC context) → if `NONE`, no setup; else `scoring_model.score` → `trade_planner.plan` → `Setup`. Runner collects setups, filters/ranks, returns `RunResult`.

**Preserved architecture:** all new analysis modules live in the core package free of `typer`/`rich`; `test_core_independence.py` is extended to cover them. The `MarketDataProvider` seam keeps the network mockable, exactly as `AnalysisProvider` did in v1.

### Automated Testing Decisions

**What makes a good test here:** assert external behavior of each module's public interface (inputs → outputs), never internal mechanics. The pure modules carry the model's risk and are tested exhaustively with fabricated candle frames / feature inputs; the network is never hit — it is replaced at the `MarketDataProvider` and `AnalysisProvider` seams. `pytest` is the framework; `tests/` already holds the prior art.

Modules to be tested:
- **`indicators.py` (unit)** — feed hand-built OHLCV frames with known outcomes; assert EMA ordering, RSI/MACD sign, ATR positivity, OBV slope, relative-volume ratio. Prior art: `tests/test_scoring.py` (pure-logic pinned values).
- **`structure.py` (unit)** — synthetic higher-high/higher-low and lower-high/lower-low series → assert `BULLISH`/`BEARISH`/`BROKEN`.
- **`direction.py` (unit)** — assert agreement → `LONG`/`SHORT`, 4h/1d conflict → `NONE`, and BTC veto flips an otherwise-directional result to `NONE`. Prior art: `tests/test_ranking.py` (boundary-focused pure logic).
- **`scoring_model.py` (unit)** — pinned category fractions → assert total and breakdown; assert weight changes move the total; assert threshold gating (a below-threshold or `NONE`-direction coin is not a signal). Prior art: `tests/test_scoring.py`.
- **`trade_planner.py` (unit)** — for a known structure + ATR, assert stop below invalidation (long) with the buffer applied, take-profit at/beyond the R:R floor, and the computed R:R value.
- **`config.py` (unit)** — new fields parse and default correctly; invalid weights/threshold/`target_rr` raise `ConfigError`; retired buy/sell keys are rejected as unknown. Prior art: `tests/test_config.py`.
- **`runner.py` (unit/integration with mocks)** — inject fake `MarketDataProvider` + `AnalysisProvider`: assert full pipeline produces ranked setups; a TradingView-only failure still scores/surfaces the coin (degraded); a total market-data failure records a `Failure` and skips; per-source sleeps are called with the configured delays (mocked sleep, not wall-clock). Prior art: `tests/test_runner.py`.
- **`CcxtBinanceProvider` (unit)** — map a canned `ccxt`-shaped OHLCV + order-book response into `Candles`/`OrderBook`; no network. Prior art: `tests/test_provider.py` (`map_summary` canned-response test).
- **Core independence** — extend `tests/test_core_independence.py` so importing every new core module succeeds with `typer`/`rich` blocked.

CLI/presentation and the concrete network calls (real `ccxt`/`tradingview-ta` over the wire) are not unit-tested (thin, I/O-bound), consistent with v1.
