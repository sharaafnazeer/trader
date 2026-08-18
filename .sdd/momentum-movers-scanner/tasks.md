# Tasks: Momentum / Breakout Scanner ("movers")

Feature slug: `momentum-movers-scanner`
Requirements: `.sdd/momentum-movers-scanner/requirements.md`

Each task is a vertical slice through the new momentum path (score → rank → CLI) with observable behavior. Tasks are in dependency order. This is a **second, independent scanner**; the trend `scan`, backtester, scoring/direction engines, and data layer must be left unchanged (the trade planner is reused, not modified — or extended additively). Quality gates are identical and deterministic: test suite, linter, type checker, byte-compile.

---

## Task 01-momentum-score-and-movers-command

Stand up the momentum engine end-to-end: score each watchlist coin 0–100 on a composite of relative-strength-vs-BTC, breakout, volume expansion, and acceleration; filter out illiquid coins; tag a direction; and surface the threshold-clearing movers as a ranked list from a new `movers` command. This is the walking tracer bullet — a ranked momentum list (no trade plans yet).

### Implementation steps

- [x] Implement a pure `MomentumModel` producing a 0–100 composite score (relative-strength-vs-BTC + breakout + volume expansion + acceleration, each fractional × configurable weight) plus a direction (up-momentum → LONG, down-momentum → SHORT) and a per-factor breakdown; add pure helpers for relative strength (coin return vs BTC return) and `breakout_fraction(close, trailing_high_or_low, atr)` (ATR-normalized distance past the trailing N-day high/low), reusing `compute_features` for relative volume / ROC / ATR.
- [x] Add configuration: momentum factor weights (defaults RS 40 / breakout 30 / volume 20 / acceleration 10), `momentum_threshold` (default ~60), and lookbacks (relative-strength and breakout day windows); with validation and unknown-key rejection. Reuse `min_depth`/`max_spread` for the liquidity filter.
- [x] Implement `run_movers`: fetch the BTC benchmark return once, then per coin fetch live daily candles + one order-book snapshot, apply the liquidity filter, score via `MomentumModel`, and rank by score descending, surfacing those at/above the threshold with their direction. The returned `MoversRun` **retains filtered and below-threshold coins** (not just the surfaced ones) so Task 02's `--all` is purely additive.
- [x] Add a `movers` CLI command that runs it over the configured watchlist and prints the ranked surfaced list (rank · symbol · direction · momentum score).
- [x] Add unit tests for the momentum model, helpers, liquidity filter, config validation, and a `run_movers` orchestration test with mocked providers; extend the core-independence test to cover the new module.

### Acceptance criteria

- [x] With two coins of **identical raw return** — one where BTC's return is low (so the coin outperforms) and one where BTC's return matches the coin (market-neutral) — the outperformer has the higher relative-strength factor credit *and* the higher total score (tested, holding raw return constant so it isolates the RS effect); and a coin breaking out on expanding volume scores higher than one drifting up on flat volume (tested).
- [x] Increasing a factor's weight increases the score for a coin earning credit in that factor; up-momentum is tagged LONG and down-momentum SHORT; the per-factor breakdown is populated (tested).
- [x] A coin below `min_depth` or above `max_spread` is filtered out of the surfaced movers, and coins below `momentum_threshold` are excluded; survivors are ranked by score descending (tested).
- [x] `run_movers` fetches the BTC benchmark return **exactly once regardless of watchlist size** (run with ≥2 coins; assert BTC `get_ohlcv` call count == 1) and scores each coin relative to it (tested via mocked providers).
- [x] The `movers` command over a mocked/injected watchlist prints a ranked momentum list, exit code 0 (tested).
- [x] Momentum config (weights, threshold, lookbacks) parses with documented defaults; invalid values raise a clear configuration error and an unknown key is rejected (tested).
- [x] The new momentum module imports with `typer` and `rich` blocked (core-independence test passes).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 02-mover-trade-plans-and-output

Make each surfaced mover actionable and the output complete: attach a concrete trade plan (entry, a stop placed relative to the breakout level / ATR, and a target), honor the long-only toggle, and add the `--all` and `--json` output controls.

### Implementation steps

- [x] Attach a trade plan to each surfaced mover by reusing `TradePlanner` with a **breakout/ATR-based invalidation** — stop = trailing breakout level ∓ `atr_buffer × ATR` (below for a long, above for a short), target floored by `target_rr`. Reuse the **existing** `atr_buffer` and `target_rr` config keys (no new config). If cleanest, extend `TradePlanner.plan` with an optional explicit invalidation level (additive, backward-compatible — the trend `scan` path unchanged).
- [x] Apply the `long_only` toggle so short (down-momentum) movers are suppressed when enabled.
- [x] Add `--all` (include below-threshold and filtered coins) and `--json` (structured record derived from the same in-memory result) to the `movers` command; render entry / stop / target columns for surfaced movers.
- [x] Add unit tests for the breakout/ATR plan, the long-only filter, and the `--all` / `--json` output.

### Acceptance criteria

- [x] For pinned candles, a long mover's stop equals `trailing_high − atr_buffer × ATR` and a short mover's stop equals `trailing_low + atr_buffer × ATR` (exact arithmetic, both tested), and the plan's `(target − entry)/(entry − stop)` (long; mirrored short) is ≥ `target_rr` (tested).
- [x] With `long_only` enabled, no short (down-momentum) mover is surfaced (tested).
- [x] `--all` includes below-threshold and liquidity-filtered coins while the default view excludes them (tested).
- [x] `--json` writes a record that reloads to the same movers, scores, and trade plans shown in the table (tested).
- [x] The `movers` command renders entry / stop / target for each surfaced mover, exit code 0 (tested).
- [x] The output (table and `--json`) includes the advisory / top-buying-risk disclaimer (asserted, e.g. the disclaimer text appears in the rendered output and JSON) (tested).
- [x] No regression: the trend `scan` and backtester behave unchanged, and the reused `TradePlanner` change (if any) is additive — existing tests stay green (tested).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.
