# Requirements: Configurable Direction Rule & Trading Profiles

> Evolves the `multi-factor-scoring-engine` feature. That feature scores coins 0–100 and
> emits directional setups, but its direction gate ("both higher timeframes must fully
> agree on EMA stack and market structure") proved too strict in live testing — it
> returned "no trade" for all 20 coins in a clearly directional market. This change
> replaces that gate with a tunable lead-timeframe rule and makes the analysis
> timeframes follow the trading style (futures vs spot). It modifies existing modules;
> the scoring model, trade-planner geometry, and advisory-only stance are unchanged.

## Problem Statement

As a crypto trader, the scoring engine almost never shows me a setup. Its direction rule demands that the 4-hour and daily timeframes both independently agree on trend *and* structure at the same moment — which rarely happens, so even in an obviously trending (broadly bearish) market it reports "no trade" for every coin. It is so conservative it is effectively silent. Separately, it only ever looks at 4-hour and daily timeframes, which suits a futures/short-term style but is wrong for a spot investor who cares about weekly and monthly trend. And when it does say "no trade", it doesn't tell me *why*, so I can't tell a genuine no-setup from an over-strict filter.

## Solution

Replace the rigid direction rule with a tunable "lead timeframe" rule: the highest timeframe decides the direction, and the next timeframe down only has to *not contradict* it (rather than fully confirm it), so real trends surface instead of being filtered into silence. Make the set of timeframes follow the trading style through two presets — **futures** (15m/1h/4h/1d) and **spot** (1d/1w/1M) — with the leading timeframe and the level-drawing timeframe scaled to match, and manual overrides for power users. Keep the market-context (BTC) safety veto, but make it configurable. When history is too short to compute a long-horizon indicator (common on weekly/monthly for younger coins), degrade gracefully and mark the result as lower-confidence rather than hiding the coin. And whenever a coin is *not* surfaced, show a short reason so the trader understands the gate. The knobs default to the conservative-but-usable settings, so the tool works well out of the box while remaining tunable.

## User Stories

1. As a trader, I want the highest timeframe to decide the trade direction, so that I trade with the dominant trend.
2. As a trader, I want the next-lower timeframe only to *not oppose* the lead (rather than fully confirm it), so that clean trends are not filtered into silence.
3. As a conservative trader, I want an option to require the lower timeframe to fully confirm the lead, so that I can tighten the filter when I want fewer, higher-agreement signals.
4. As a futures trader, I want a preset that analyzes 15m/1h/4h/1d with the daily leading, so that the tool matches my short-to-mid-term style out of the box.
5. As a spot investor, I want a preset that analyzes daily/weekly/monthly with the monthly leading, so that direction reflects my long-term horizon.
6. As a trader, I want the trading-style preset to also set the timeframe used to draw entry/stop/target levels, so that my levels match the horizon I am trading.
7. As a power user, I want to override the preset's timeframes, leading timeframe, and level timeframe by hand, so that I am not boxed in by the presets.
8. As a trader, I want direction within a timeframe to be driven by the moving-average trend, with structure only vetoing when it clearly contradicts, so that a choppy-but-trending market still produces a direction.
9. As a spot investor holding newer coins, I want the tool to still evaluate a coin when it lacks the years of history a long-horizon average needs, so that young coins are not silently excluded.
10. As a trader, I want any coin evaluated on reduced history to be marked as lower-confidence, so that I know to trust it less.
11. As a trader, I want the market-context (BTC) veto kept on by default, so that I do not take trades against the whole market.
12. As a trader, I want to turn the BTC veto off, so that I can run market-agnostic or BTC-only scans.
13. As a trader, I want BTC's own regime judged on the same timeframes I am trading, so that the veto reflects my horizon.
14. As a trader, I want every coin that is *not* surfaced to show a short reason (lead undecided, lower timeframe opposed, vetoed by the market, or limited history), so that I understand the gate and can tell a real no-setup from an over-strict filter.
15. As a trader, I want the relaxed rule to surface the genuinely trending coins that the old rule hid, so that the scanner is actually useful in a trending market.
16. As a trader, I want sensible defaults (futures preset, must-not-oppose filter, BTC veto on), so that the tool is useful before I configure anything.
17. As a trader, I want the change to leave the scoring model, trade-plan levels, and advisory-only behavior intact, so that only the direction logic and timeframe selection change.

## User Acceptance Tests

1. Given a coin whose leading timeframe shows a clear uptrend and whose lower timeframe is neutral (undecided), when it is analyzed with default settings, then its direction is long and it can be surfaced.
2. Given a coin whose leading timeframe is bullish but whose lower timeframe is clearly bearish, when it is analyzed, then its direction is none (the lower timeframe opposed the lead).
3. Given the "require confirmation" option is enabled, when the lower timeframe is neutral rather than agreeing, then the coin is not surfaced.
4. Given the futures preset (default), when the watchlist is analyzed, then the 4-hour and daily timeframes are used and the daily leads.
5. Given the spot preset, when the watchlist is analyzed, then daily/weekly/monthly timeframes are used and the monthly leads.
6. Given the spot preset, when a surfaced setup's levels are shown, then the entry/stop/target are drawn from weekly structure rather than 4-hour structure.
7. Given a coin whose leading-timeframe long-horizon average cannot be computed for lack of history, when it is analyzed, then it is still evaluated and flagged as limited-history rather than dropped.
8. Given the market (BTC) is trending down and the BTC veto is on, when an otherwise-bullish altcoin is analyzed, then its long is vetoed.
9. Given the BTC veto is turned off, when the same altcoin is analyzed, then its long is no longer vetoed on market grounds.
10. Given a coin that is not surfaced, when the full list is shown, then that coin displays a short reason for not qualifying.
11. Given the same broadly-bearish market snapshot on which the old rule surfaced nothing across a 20-coin watchlist, when analyzed with the relaxed default rule, then at least the coins whose daily trend is decided and un-opposed are surfaced with a direction.
12. Given manual overrides for timeframes and leading timeframe, when the watchlist is analyzed, then the overrides take precedence over the selected preset.
13. Given default settings and a coin whose moving-average trend is clearly directional but whose recent swings are choppy/broken (not opposing), when it is analyzed, then a direction is still assigned.

## Definition of Done

- All user acceptance tests pass.
- The strict "both higher timeframes must fully agree" rule is removed and replaced by the single lead-timeframe rule.
- The leading timeframe is the highest in the active set; the lower higher-timeframe filters as must-not-oppose by default, switchable to must-confirm.
- Within a timeframe, direction is driven by the EMA stack and vetoed only by clearly opposing structure.
- `futures` and `spot` presets set the timeframe set, higher-timeframe subset, leading timeframe, and level-drawing timeframe; `futures` is the default; explicit overrides win.
- Insufficient history degrades gracefully (shorter average / neutral structure) and is marked limited-history rather than excluding the coin.
- The BTC veto is configurable (default on) and judged on the active profile's timeframes.
- Every non-surfaced coin shows a concise reason (lead undecided / lower timeframe opposed / market veto / limited history) in the full and detailed views and the JSON record.
- On the previously-failing 20-coin bearish snapshot, the relaxed default rule surfaces the coins whose lead is decided and un-opposed (validated: BNB, DOT, ATOM as shorts in the planning prototype).
- The scoring model, trade-plan geometry, CLI flags, stateless output, and advisory-only behavior are unchanged; no regression in existing tests.
- Automated tests cover the direction rule (all branches and reasons), profile resolution and config validation, limited-history degradation, structure's insufficient-swings handling, and runner integration; all quality gates pass.

## Out of Scope

- Any change to the eight scoring categories or their weights.
- Any change to the trade-plan geometry (entry / stop / take-profit formulas) beyond which timeframe supplies the swing levels.
- Backtesting, win-rate measurement, or any historical validation of signal profitability (still deferred with the persistence/backtesting work).
- Trade execution and exchange keys.
- Additional presets beyond `futures` and `spot`, and market-context inputs beyond BTC.
- A literal one-year candle (not offered by the exchange); the long horizon is expressed with weekly/monthly candles.
- Push notifications and any non-CLI interface.

## Further Notes

- This change was motivated by a live run: against 20 liquid USDT pairs the old rule surfaced zero setups because 18/20 coins sat below their long-term trend and no coin had both higher timeframes cleanly agreeing at once. A planning prototype of the relaxed rule surfaced three shorts (BNB, DOT, ATOM), confirming the fix.
- The relaxed rule is deliberately still conservative: the lead must resolve a direction, the lower timeframe must not oppose it, and the market veto stays on by default. It widens coverage without inventing signals.
- The tool remains advisory and heuristic. Surfacing a setup is not a prediction of profit; see the win-rate note — no profitability has been measured, and measuring it is the deferred backtesting work.

---

## Technical Annex
> Written against codebase as of: 2026-07-28

Amends the `multi-factor-scoring-engine` implementation under `src/trader/`. When generating tasks, verify each decision against the current code and flag conflicts.

### Current state (baseline)

- `direction.py` — `decide(features_by_tf, structure_by_tf, btc_context, *, htf_timeframes=("4h","1d")) -> Direction`; requires every HTF to resolve the *same* non-NONE direction, and `_timeframe_direction` requires EMA stack **and** structure to agree. `MarketContext(btc_direction)`. This is the strict rule being replaced.
- `config.py` — frozen `Config` with `timeframes`, `weights`/`category_weights`, `quality_threshold`, per-source delays, `ohlcv_lookback`, order-book cutoffs, indicator params, `atr_buffer`, `target_rr`, `reference_timeframe` (default `4h`), `long_only`; `load_config` with `ConfigError`, unknown-key rejection, and typed validators.
- `indicators.py` — `compute_features(candles) -> TimeframeFeatures` (EMA 20/50/200, RSI, MACD, ROC, ATR, OBV+slope, Bollinger width, relative volume).
- `structure.py` — `analyze(candles) -> StructureState` (BULLISH/BEARISH/BROKEN; `swing_highs`/`swing_lows`; `last_swing_high`/`last_swing_low`).
- `runner.py` — `run_analysis(...)` orchestrates fetch → features → structure → direction → score → plan; builds `CoinAnalysis`/`AnalysisRun`; per-source throttling; fetches BTC context once.
- `cli.py` — `scan` command with `--config`/`--details`/`--all`/`--json`/`--watch`; `analysis_run_to_dict`.
- Tooling: Python ≥3.11, `uv`; `pytest`/`ruff`/`mypy --strict`; analysis core free of `typer`/`rich`, enforced by `tests/test_core_independence.py`.

### Architectural Decisions

**Direction rule (`direction.py`) — the core change.**
- Replace `decide`. New signature (indicative): `decide(features_by_tf, structure_by_tf, btc_context, *, htf_timeframes, lead_timeframe, require_confirmation=False, btc_veto=True) -> DirectionResult`.
- New `DirectionResult` (frozen): `direction: Direction` plus `reason: str | None` (e.g. `"lead_unresolved"`, `"filter_opposed"`, `"btc_veto"`, `"limited_history"`, `None` when surfaced). Enables the NONE-reason annotation without a second pass.
- Algorithm (encodes the validated prototype):
  1. Resolve the **lead** timeframe's direction via the revised `_timeframe_direction`; if NONE → `reason="lead_unresolved"`.
  2. For each non-lead HTF: if it resolves the **opposite** direction → `reason="filter_opposed"` (NONE). Neutral/absent is allowed unless `require_confirmation=True`, in which case a non-matching timeframe → `reason="filter_unconfirmed"`.
  3. BTC veto (when `btc_veto`): opposing BTC regime → `reason="btc_veto"`. BTC never vetoes its own row.
- Revise `_timeframe_direction`: **EMA stack drives** the direction; **structure vetoes only when opposite** (BROKEN/neutral allowed). Applied at both lead and filter.

Prototype (validated live; encodes the decision, not a full impl):
```
lead_dir = timeframe_direction(lead)            # stack drives; structure must-not-oppose
if lead_dir is NONE: return NONE, "lead_unresolved"
opp = SHORT if lead_dir is LONG else LONG
for tf in other_htfs:
    d = timeframe_direction(tf)
    if d is opp: return NONE, "filter_opposed"
    if require_confirmation and d is not lead_dir: return NONE, "filter_unconfirmed"
if btc_veto and btc_dir is opp: return NONE, "btc_veto"
return lead_dir, None
```

**Trading profiles (`config.py`).**
- Add `profile: "futures" | "spot"` (default `"futures"`). A pure `resolve_profile(profile, overrides) -> ResolvedProfile` expands the preset into `timeframes`, `htf_timeframes`, `lead_timeframe`, `reference_timeframe`:
  - `futures` → timeframes `["15m","1h","4h","1d"]`, htf `["4h","1d"]`, lead `"1d"`, reference `"4h"`.
  - `spot` → timeframes `["1d","1w","1M"]`, htf `["1w","1M"]`, lead `"1M"`, reference `"1w"`.
- Lead = highest timeframe in the set. Explicit `timeframes`/`htf_timeframes`/`lead_timeframe`/`reference_timeframe` in YAML override the preset.
- Add `require_confirmation: bool` (default `false`) and `btc_veto: bool` (default `true`). Validate `profile` ∈ {futures, spot}; `lead_timeframe` ∈ `timeframes`; `htf_timeframes` ⊆ `timeframes`.

**Limited history (`indicators.py`, `structure.py`).**
- `compute_features` degrades when candle count < the longest EMA period: fall back to the longest computable stack (e.g. 20/50 when 200 is unavailable) and set a `limited_history: bool` on `TimeframeFeatures`.
- `structure.analyze` returns a neutral/insufficient state (treated as not-opposing) when fewer than the minimum swings are present, rather than a hard BULLISH/BEARISH.

**Orchestration (`runner.py`).**
- Thread the resolved profile (timeframes/htf/lead/reference), `require_confirmation`, and `btc_veto` through. Compute BTC context on the profile's timeframes with the same relaxed rule. Propagate `DirectionResult.reason` and any `limited_history` marker onto `CoinAnalysis`. Use the profile's `reference_timeframe` for the trade plan.

**CLI (`cli.py`).**
- In `--all`/`--details` and `analysis_run_to_dict`, render the NONE-reason per non-surfaced coin and a limited-history marker on affected rows. No new flags.

### Automated Testing Decisions

Test external behavior of each unit; pin inputs; no network (mock `MarketDataProvider`/`AnalysisProvider`; hand-built frames). `pytest`; prior art in `tests/test_direction.py`, `tests/test_config.py`, `tests/test_indicators.py`, `tests/test_structure.py`, `tests/test_runner_analysis.py`.

- **`direction.py`** — lead resolves → LONG/SHORT; filter opposed → NONE (`filter_opposed`); filter neutral allowed by default but blocked under `require_confirmation` (`filter_unconfirmed`); BTC veto on/off (`btc_veto`); lead unresolved (`lead_unresolved`); revised `_timeframe_direction` (stack drives, broken structure allowed, opposing structure vetoes). Assert both `direction` and `reason`.
- **`config.py`** — `resolve_profile` for `futures`/`spot` yields the documented timeframes/lead/reference; overrides beat the preset; invalid `profile`, `lead_timeframe` not in `timeframes`, `htf_timeframes` ⊄ `timeframes` raise `ConfigError`; `require_confirmation`/`btc_veto` parse with defaults.
- **`indicators.py`** — short frame yields `limited_history=True` and a fallback stack; full frame yields `limited_history=False`.
- **`structure.py`** — too-few-swings frame yields the neutral/insufficient state (not-opposing), not BULLISH/BEARISH.
- **`runner.py`** (mocked providers) — spot profile drives weekly/monthly fetches and monthly lead; a coin whose lead is decided and un-opposed surfaces while an opposed one does not; NONE-reason and limited-history marker propagate to `CoinAnalysis`; BTC context computed on the profile timeframes.
- CLI/presentation and live network calls remain untested (thin, I/O-bound), consistent with the baseline.
