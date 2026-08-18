# Requirements: Momentum / Breakout Scanner ("movers")

> A **second, independent scanner** alongside the trend-following `scan`. The existing
> engine is a conservative trend-follower — it deliberately avoids the volatile, fast
> movers and, as a live backtest confirmed, actually *loses* on them (e.g. BOME +36%/30d
> but −0.26R on the trend rule; LDO +19%/30d but −0.49R). Those high-momentum coins are
> exactly what active traders are catching. This feature adds a momentum/breakout scanner
> that ranks coins by *what's actually moving* — the opposite selection criterion from the
> trend engine. It's a live scanner in v1; a momentum backtest is a deferred follow-up.

## Problem Statement

As a crypto trader, the trend-following scanner points me at "already in a clean trend"
coins that often move slowly, while the coins that are actually running — the ones other
active traders are profiting from — never show up, because the trend rule distrusts fast,
volatile breakouts and gets whipsawed out of them. I want a scanner that surfaces the
strong movers (coins breaking out and outperforming the market on real volume) so I can
see the momentum plays the trend engine is structurally built to miss.

## Solution

A `movers` scanner that scores each watchlist coin 0–100 on **momentum** — combining how
strongly it's outperforming the market (relative strength vs BTC), whether it's breaking
out to new recent highs, whether volume is expanding to confirm the move, and whether the
move is accelerating. It filters out coins too illiquid to trade, ranks the rest, tags a
direction (a strong up-move is a long candidate, a strong down-move a short candidate),
and — for coins clearing a configurable momentum threshold — attaches a concrete trade
plan (entry, a stop placed below the breakout level / by ATR, and a target). It's a
separate command from the trend `scan`, using the opposite philosophy: **surface what's
moving**, not what's in a proven slow trend. It remains advisory, and — like everything
else — its edge is unproven until backtested (momentum has its own risk: you're often
entering after a big run, near potential tops).

## User Stories

1. As a trader, I want a scanner that ranks coins by momentum, so that I can see what's actually moving instead of only slow established trends.
2. As a trader, I want the momentum score to reward coins outperforming the market (not just riding a market-wide pump), so that I find genuine leaders.
3. As a trader, I want breakouts to new recent highs factored in, so that I catch moves as they start rather than only after a big run.
4. As a trader, I want volume expansion factored in, so that I trust moves backed by real participation over thin drifts.
5. As a trader, I want acceleration factored in, so that speeding-up moves rank above fading ones.
6. As a trader, I want illiquid coins filtered out, so that I'm not shown pumps I can't actually enter or exit.
7. As a trader, I want the momentum factor weights configurable, so that I can tune the scanner to my style (e.g. weight breakouts higher for earlier entries).
8. As a trader, I want each mover tagged as a long (up-momentum) or short (down-momentum) candidate, so that I know which way to trade it.
9. As a trader, I want an option to see only long (up) movers, so that I can ignore shorts when I only trade long.
10. As a trader, I want a concrete trade plan (entry, stop, target) for each surfaced mover, so that I know how to trade it, not just that it's moving.
11. As a trader, I want the stop placed relative to the breakout level / volatility, so that it fits a momentum trade rather than a slow-trend swing.
12. As a trader, I want a configurable momentum threshold, so that only strong movers are surfaced and I'm not flooded with weak ones.
13. As a trader, I want this as a separate `movers` command, so that it's clearly distinct from the trend-following `scan`.
14. As a trader, I want to point it at my own watchlist, so that it ranks the coins I care about.
15. As a trader, I want the scanner to be clear that it surfaces movers but doesn't guarantee profit, so that I don't mistake "moving" for "will keep moving."
16. As a trader, I want the trend `scan` and the rest of the tool left unchanged, so that adding this carries no risk to what already works.

## User Acceptance Tests

1. Given a watchlist, when the movers scanner runs, then coins are ranked by a 0–100 momentum score with the strongest movers at the top.
2. Given two coins with the same raw return but one strongly outperforming BTC and one merely matching the market, when they are scored, then the outperformer scores higher.
3. Given a coin breaking to a new recent high on expanding volume, when it is scored, then it scores higher than a coin drifting up on flat volume.
4. Given a coin whose order-book liquidity is below the configured minimum, when the scanner runs, then that coin is filtered out (not shown as a tradeable mover).
5. Given a strong up-move, when it is surfaced, then it is tagged as a long candidate; given a strong down-move, then it is tagged as a short candidate.
6. Given the long-only option, when the scanner runs, then no down-momentum (short) movers are shown.
7. Given a coin that clears the momentum threshold, when it is displayed, then it shows a trade plan with entry, stop (relative to the breakout level / ATR), and target.
8. Given the momentum threshold is raised, when the scanner runs, then fewer or equal coins are surfaced.
9. Given the factor weights are changed in configuration, when the same coin is scored, then its momentum score changes accordingly.
10. Given the movers scanner is run, when the trend `scan` and backtest are subsequently run, then their behavior and results are unchanged by this feature.
11. Given a broadly volatile market, when the scanner runs, then coins the trend `scan` marks NONE/low (e.g. high-volatility breakouts) can appear as high-momentum movers here — the two scanners surface different coins by design.

## Definition of Done

- A `movers` command ranks watchlist coins 0–100 by a composite momentum score (relative strength vs BTC, breakout, volume expansion, acceleration), with configurable weights.
- Coins failing a configurable liquidity filter are excluded; coins are tagged long (up) or short (down); a long-only option suppresses shorts.
- Coins clearing a configurable momentum threshold are surfaced with a trade plan (entry, stop relative to breakout/ATR, target); the default view is the ranked surfaced list.
- The scanner points at the configured watchlist and uses live data (no dependence on the historical cache).
- The trend `scan`, backtest, scoring/direction/trade-plan logic, and data layer behave exactly as before; no regression in existing tests.
- The output/report states that surfaced movers are advisory and momentum carries top-buying risk; its edge is unproven pending a (deferred) momentum backtest.
- Automated tests cover the momentum scoring (each factor, weight sensitivity, direction, threshold gating), the breakout and relative-strength calculations, the liquidity filter, configuration validation, and a movers-orchestration test with mocked providers; all quality gates pass.

## Out of Scope

- A **momentum backtest** (validating the movers signals over history) — a deferred follow-up; v1 is a live scanner only.
- Any change to the trend-following `scan`, the backtester, or the scoring/direction/trade-plan engines beyond reusing the trade planner.
- Trade execution / order placement.
- Predicting *future* movers or replicating discretionary trading skill — this ranks current, measurable momentum, not forecasts.
- Additional data sources beyond Binance (and BTC as the market benchmark) — no TradingView in the movers path.
- Persisting momentum scores / alerting / notifications.

## Further Notes

- Motivated directly by live data: the trend engine's high-expectancy coins (SEI, OP, ADA, FLOKI) are slow/steady names, while the coins active traders profit on (BOME +36%/30d, LDO +19%/30d) score *negatively* on the trend rule because they whipsaw it. Momentum is almost the inverse selection criterion — hence a separate engine, not a tweak.
- Honest limitation: a scanner surfaces what is *already* moving; momentum entries are often late (near tops) and momentum strategies can blow up on reversals. The breakout-relative stop caps per-trade risk, but the *strategy* edge is unproven until the deferred momentum backtest is built and run.
- Relative strength is measured **vs BTC** so a coin merely rising with a market-wide pump doesn't rank as a leader.

---

## Technical Annex
> Written against codebase as of: 2026-08-03

Adds to the existing `src/trader/` codebase. When generating tasks, verify each decision against the current code and flag conflicts.

### Current state (baseline)

- `indicators.py` — `compute_features(candles) -> TimeframeFeatures` already provides `atr`, `relative_volume`, `roc`, `obv_slope`, EMAs, etc. (reusable for volume/acceleration factors).
- `market_data.py` — `CcxtBinanceProvider` (live per-symbol `get_ohlcv` + `get_order_book`); `OrderBook` (depth/spread). Scan-style live fetch (~`ohlcv_lookback` candles), no cache dependency.
- `scoring_model.py` — has a fractional-credit → weighted-points pattern (prior art for a composite 0–100 model) and a `_liquidity_fraction`/order-book gate to reuse conceptually.
- `trade_planner.py` — `TradePlanner.plan(direction, candles, pivots, atr, ...)` produces entry/stop/take-profit/RR; reusable, but momentum wants the stop at the breakout level / ATR rather than swing invalidation.
- `config.py` — frozen `Config` + `load_config` (ConfigError, unknown-key rejection, typed validators); has `min_depth`, `max_spread`, `long_only`, `ohlcv_lookback`.
- `cli.py` — `scan`/`backtest`/`refresh-cache`/`market-data` typer commands; `-v`/`--log-file` logging; the analysis core is free of `typer`/`rich` (enforced by `tests/test_core_independence.py`).
- `runner.py` — `run_analysis` orchestrates the trend scan; BTC context is fetched there (prior art for a one-shot BTC benchmark fetch).
- Tooling: Python ≥3.11, `uv`; `pytest`/`ruff`/`mypy --strict`; ~235 tests green.

### Architectural Decisions

**New pure module — `momentum.py` (analysis core, no `typer`/`rich`).**
- `MomentumModel.score(symbol, candles, btc_return, weights, ...) -> MomentumScore` where `MomentumScore` (frozen) carries `symbol`, `score` (0–100), `direction` (LONG for up-momentum, SHORT for down), and a per-factor breakdown.
- Composite (fractional-credit × weight, mirroring `scoring_model`): **relative_strength** (coin return − BTC return over the lookback, normalized), **breakout** (how far close is above the trailing N-day high, or below the low for shorts), **volume** (relative volume vs average), **acceleration** (ROC / rising momentum). Default weights RS 40 / breakout 30 / volume 20 / acceleration 10, configurable.
- Direction from the sign of the dominant momentum (up → LONG, down → SHORT); score is magnitude of momentum strength.
- Pure helpers (tested directly): `relative_strength(coin_return, btc_return)`, `breakout_fraction(close, trailing_high/low, atr)`. Reuse `compute_features` for `relative_volume`/`roc`/`atr`.

**Liquidity filter.** Reuse the order-book depth/spread gate (`min_depth`/`max_spread`): a coin failing it is excluded from the ranked movers (not scored to zero — filtered out), matching UAT #4.

**Orchestration — `run_movers(...)` (in `runner.py` or a small new module).**
- Fetch the **BTC benchmark return** once (BTC daily candles over the lookback) → `btc_return`.
- Per coin: fetch daily candles (live, ~`ohlcv_lookback`) + one order-book snapshot; apply the liquidity filter; `MomentumModel.score(...)`; for coins ≥ `momentum_threshold` and (respecting `long_only`) attach a `TradePlan`.
- Return a ranked `MoversRun` (surfaced movers + filtered/failed). Reuses the injectable-provider + per-source-throttle pattern; no historical cache.

**Trade plan for movers.** Reuse `TradePlanner`, but supply a **breakout/ATR-based invalidation** (stop below the breakout level minus an ATR buffer for longs; mirror for shorts) instead of the swing-low invalidation the trend planner uses. Target by the configured `target_rr`. (Either extend `TradePlanner.plan` to accept an explicit invalidation level, or add a thin momentum-plan helper — decide at task time; prefer reusing `plan` with an injected invalidation.)

**Config (`config.py`).** Add `momentum_weights` (RS/breakout/volume/acceleration, defaults above), `momentum_threshold` (default ~60), `rs_lookback_days` (e.g. 30) and `breakout_lookback_days` (e.g. 20). Reuse `min_depth`/`max_spread`/`long_only`/`ohlcv_lookback`. Validation + unknown-key rejection.

**CLI (`cli.py`).** New `movers` command: `--config`, `--all` (show below-threshold too), `--json`, plus reuse `-v`/`--log-file`. Renders a ranked table (rank · symbol · direction · momentum score · entry/SL/TP) with the advisory/top-buying-risk disclaimer.

**Data flow:** `run_movers` → fetch BTC return once → per coin: live candles + order book → liquidity filter → `MomentumModel.score(coin_return vs btc_return, breakout, volume, acceleration)` → threshold + direction (+ `long_only`) → `TradePlanner` (breakout/ATR stop) → ranked `MoversRun` → CLI table/JSON.

**Scope.** New `momentum.py` + `run_movers` + `movers` command + config additions. The trend `scan`/`run_analysis`, backtester, scoring/direction/trade-plan engines, and data layer are unchanged (trade_planner reused, not modified — unless an injected-invalidation param is the cleanest path, in which case it's additive/backward-compatible).

### Automated Testing Decisions

Test external behavior with pinned inputs; no network — inject fake providers + hand-built candle frames; `pytest`. Prior art: `tests/test_scoring_model.py` (composite fractional scoring), `tests/test_trade_planner.py` (levels), `tests/test_runner_analysis.py` (mocked providers), `tests/test_config*.py` (validation).

- **`MomentumModel` (unit).** Pinned inputs → each factor's fractional credit and the weighted 0–100 total; a coin outperforming BTC scores higher than one merely matching the market (RS effect); breakout+volume raises the score vs flat-volume drift; weight changes move the total; direction sign (up→LONG, down→SHORT); threshold gating (below-threshold not surfaced).
- **Momentum helpers (unit).** `relative_strength` and `breakout_fraction` on pinned values (incl. new-high vs mid-range, and the short mirror).
- **Liquidity filter (unit).** A coin below `min_depth` or above `max_spread` is excluded from the surfaced movers.
- **`config` (unit).** `momentum_weights`/`momentum_threshold`/lookbacks parse with defaults; invalid values raise `ConfigError`; unknown keys rejected.
- **`run_movers` (integration, mocked providers).** BTC return fetched once; a strong up-mover surfaces LONG with a plan; a low-liquidity coin is filtered; `long_only` suppresses shorts; ranking is by score descending.
- **Core-independence.** `momentum.py` imports with `typer`/`rich` blocked (extend `tests/test_core_independence.py`).
- CLI/presentation and live network calls untested (thin/I-O-bound), consistent with the codebase.
