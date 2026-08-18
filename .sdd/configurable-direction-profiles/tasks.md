# Tasks: Configurable Direction Rule & Trading Profiles

Feature slug: `configurable-direction-profiles`
Requirements: `.sdd/configurable-direction-profiles/requirements.md`

Each task is a vertical slice that cuts end-to-end (config/data → direction → orchestration → CLI output) and grows behavior with real, observable results. Tasks are in dependency order. New settings use in-code defaults in the task that introduces them, then move to YAML where noted. Quality gates are identical and deterministic across tasks: test suite, linter, type checker, byte-compile.

---

## Task 01-relaxed-direction-rule

Replace the too-strict direction gate with the tunable lead-timeframe rule, end-to-end. The highest timeframe decides direction (EMA stack drives it, structure vetoes only when clearly opposite); the other higher timeframe only has to *not oppose* it (switchable to must-confirm); the BTC veto stays. Every decision carries a reason, and the CLI surfaces that reason for non-qualifying coins — so a coin whose daily is decided and un-opposed now surfaces where the old rule showed nothing.

### Implementation steps

- [x] Revise the per-timeframe direction so the EMA stack drives the direction and structure only vetoes when it clearly opposes (BROKEN/neutral allowed).
- [x] Replace the decision function with the lead-timeframe rule returning a result that carries both the direction and a reason (`lead_unresolved`, `filter_opposed`, `filter_unconfirmed`, `btc_veto`, or none when surfaced — limited-history is a separate marker from Task 03, NOT a direction reason); add `lead_timeframe`, `require_confirmation` (default must-not-oppose), and `btc_veto` (default on) parameters with in-code defaults (lead `1d`).
- [x] Thread the new rule and its parameters through orchestration and propagate the reason onto each coin's result.
- [x] Show the non-qualifying reason per coin in the `--all`/`--details` views and the JSON record.
- [x] Add unit tests for every rule branch and reason, and a runner/CLI test that a previously-NONE coin now surfaces.

### Acceptance criteria

- [x] When the lead timeframe resolves a direction and the other higher timeframe is neutral (not opposing), the coin's direction is that direction (tested).
- [x] When the other higher timeframe resolves the opposite direction, the result is NONE with reason `filter_opposed`; when `require_confirmation` is on and it is merely neutral, the result is NONE with reason `filter_unconfirmed` (both tested).
- [x] A coin whose lead timeframe has a clean EMA stack but BROKEN structure still resolves a direction (structure only vetoes when opposite) (tested).
- [x] With the BTC veto on, an opposing BTC regime forces NONE with reason `btc_veto`; with it off, the same coin is not vetoed (both tested).
- [x] On a fixture where the lead resolves LONG/SHORT, the other higher timeframe is neutral, and BTC is non-opposing, the coin surfaces with that direction; a coin that remains NONE in the same run shows a non-null reason in both the table and the JSON record (end-to-end, exit code 0).
- [x] No regression: the existing test suite stays green with no test deletions to accommodate the change, no new CLI flags are introduced, scoring and trade-plan outputs are unchanged for an already-surfacing fixture, and the analysis core stays free of `typer`/`rich` (`tests/test_core_independence.py` passes with the new `DirectionResult` type).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 02-trading-profiles

Make the analyzed timeframes follow the trading style, end-to-end. A `futures` preset (15m/1h/4h/1d, daily leads, 4h levels) and a `spot` preset (1d/1w/1M, monthly leads, weekly levels) select the timeframe set, higher-timeframe subset, leading timeframe, and level timeframe; manual overrides win; `require_confirmation` and `btc_veto` move to YAML. Selecting `spot` makes a scan fetch and decide on weekly/monthly data.

### Implementation steps

- [x] Implement profile resolution that expands `futures`/`spot` into timeframes, higher-timeframe subset, leading timeframe, and reference (level) timeframe; the lead is the highest timeframe in the set.
- [x] Add the `profile` setting (default `futures`) plus `lead_timeframe`, `htf_timeframes`, `reference_timeframe`, `require_confirmation`, and `btc_veto` to configuration, with explicit values overriding the preset and full validation.
- [x] Thread the resolved profile through orchestration so scan uses the profile's timeframes, lead, reference timeframe, and BTC context timeframes.
- [x] Add unit tests for profile resolution, overrides, and validation, plus a runner test that the spot profile drives weekly/monthly evaluation.

### Acceptance criteria

- [x] The `futures` preset resolves to timeframes 15m/1h/4h/1d, higher timeframes 4h+1d, lead 1d, reference 4h; `spot` resolves to 1d/1w/1M, higher timeframes 1w+1M, lead 1M, reference 1w (both tested).
- [x] Explicit `timeframes`/`lead_timeframe`/`htf_timeframes`/`reference_timeframe` in config override the preset (tested).
- [x] Invalid values (`profile` not futures/spot, `lead_timeframe` not in `timeframes`, `htf_timeframes` not a subset) raise a clear configuration error (tested).
- [x] With the `spot` profile, a scan requests candles for and decides direction on the 1w and 1M timeframes, and draws trade levels from 1w (tested via mocked providers).
- [x] With the `spot` profile, the BTC market context is computed on the profile's timeframes (1w/1M), not the futures 4h/1d (tested via mocked providers).
- [x] Default configuration (no profile set) behaves as `futures`, preserving today's timeframes (tested).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 03-limited-history-degradation

Keep the tool useful on long-horizon timeframes where history is short (common on weekly/monthly for younger coins), end-to-end. When a coin lacks the candles a long moving average needs, fall back to the longest computable stack and mark the coin limited-history; when structure lacks enough swings, treat it as neutral (not opposing) rather than a hard signal. The coin is still evaluated and surfaced, with a visible limited-history marker.

### Implementation steps

- [x] Degrade feature computation when candle history is shorter than the longest moving-average period: use the longest computable stack and set a limited-history flag on the features.
- [x] Treat too-few-swings structure as neutral/insufficient (not-opposing) rather than BULLISH/BEARISH.
- [x] Propagate the limited-history marker through orchestration onto each coin's result.
- [x] Show the limited-history marker on affected rows in `--all`/`--details` and the JSON record.
- [x] Add unit tests for the degraded feature computation, the neutral structure case, and marker propagation.

### Acceptance criteria

- [x] A candle frame too short for the longest moving average yields a limited-history flag and a fallback stack, while a full frame yields no flag (both tested).
- [x] A frame with fewer than the minimum swings yields the insufficient structure state (reusing `BROKEN`, or a distinct `INSUFFICIENT` value), and the revised `_timeframe_direction` treats that state as non-opposing (returns no directional veto) — both the returned state and the non-opposing behavior are asserted (tested).
- [x] A limited-history coin is still evaluated and can surface (it is not dropped), with the marker set on its result (tested).
- [x] The limited-history marker appears on the affected rows in the `--all`/`--details` output and in the JSON record (end-to-end, exit code 0).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.
