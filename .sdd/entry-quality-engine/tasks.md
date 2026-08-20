# Entry-Quality Engine — Tasks

Derived from `.sdd/entry-quality-engine/requirements.md` (2026-08-19). Tasks are vertical
slices in topological order; the ordinal is the implementation order.

Project quality gates apply to every task: `uv run pytest`, `uv run ruff check`,
`uv run mypy src`, `uv run python -m compileall src`. So does one testing rule, stated once
here rather than repeated: **no test may perform network I/O, read a real credential, sleep
on the wall clock, or write outside a temporary directory.**

## The governing rules

**Every behavioural change lands behind a config flag defaulting off**, and each flag must be
threaded through **both** call paths — the live analysis path and the backtest evaluation path.
Wire only the live path and the measured delta is zero; wire only the backtest path and the
scan criteria fail. Each task says so explicitly because getting this half-right produces a
change that looks like a no-op when it is really just unplugged.

**Measurement is slow and therefore manual.** A full-window run over the 88-coin watchlist is
roughly **3.5 hours** (measured: ~0.0145s per point-in-time evaluation × 88 coins × ~9,855 4h
closes, replayed sequentially — only the fetch is parallel). Criteria requiring one are marked
**[manual]** and are not automatable. To keep per-task iteration tractable:

- **Iteration measurement** uses a fixed 20-coin subset config over the full window (~50 min).
  Baseline and variant must use the same config for the delta to mean anything.
- **Full-window measurement** over all 88 coins is reserved for task 07, which needs the
  baseline plus each flag plus one combination — on the order of **21 hours** of compute.

Recorded baseline (`mywatch.yaml`, 88 symbols, `2022-01-01 → 2026-07-31`, net of fees):
**1,362 trades, 35.8% win rate, +0.065R expectancy, PF 1.10, 75.1% max drawdown.**

## Scope note carried from the review

**User story 4 — "setups I would recognise as textbook pullbacks surface" — is NOT delivered
by this feature and is recorded as deferred.** Entry quality can only re-label setups that
already cleared `quality_threshold`, so the motivating BOMEUSDT case (score 51.7, rank 42 of
51) never reaches the entry scorer. Fixing that means changing what surfaces, i.e. re-tuning
the threshold or splitting surfacing from ranking — explicitly out of scope here so that the
measured deltas stay attributable. Task 06 states this in its own description rather than
implying otherwise.

---

## Task 01-measurement-harness

The trader can compare any two backtest runs and see exactly what changed. `backtest` gains
an option to load a previously-saved JSON report and print a signed delta table — trade count,
win rate, expectancy, profit factor, max drawdown — beside its own results, and refuses to
compare runs from different windows.

This is first because nothing after it can be evaluated without it.

It also has to pin the baseline down with a fixture that actually trades. The obvious choice
does not work: `backtest --demo` currently produces **zero** trades, because the demo history
sizes each timeframe by wall-clock span and leaves the daily frame with ~13 bars against a
30-bar warm-up minimum, so every evaluation resolves to no direction. A fixture asserting an
empty report would be satisfied by any change whatsoever — and five later tasks lean on it.
Either a much longer demo history or a two-timeframe config produces a fixture with trades
whose scores straddle the threshold; the task must verify a non-zero trade count.

### Implementation steps

- [x] Record the traded window, watchlist size and cost settings in the JSON backtest report, so two reports can be checked for comparability
- [x] Add a pure comparison function over two report summaries producing the signed differences in trade count, win rate, expectancy, profit factor and max drawdown
- [x] Add a comparison option to the backtest command taking the path of a previously-written report
- [x] Render the comparison after the run's own report, marking each metric as improved or regressed
- [x] Reject a baseline whose recorded window differs from the current run, naming the mismatch
- [x] Exit non-zero with the path named for a missing or malformed baseline file
- [x] Build a demo fixture that produces a non-zero number of trades, and pin its report exactly
- [x] Document the measure-a-change workflow in the README, including the iteration subset config and the full-window cost in hours

### Acceptance criteria

- [x] Comparing against a saved report prints a table containing the signed difference in trade count, win rate, expectancy, profit factor and max drawdown
- [x] An improved metric and a regressed one render with different explicit markers, asserted on the rendered text for two hand-written reports differing in each direction
- [x] A baseline whose recorded window differs is rejected with a message naming the mismatch, and no comparison table is printed
- [x] A missing baseline file exits non-zero naming the path, and a malformed one does the same rather than raising
- [x] The **terminal** output of a backtest run without the comparison option is unchanged from current behaviour (the JSON payload deliberately gains the window fields)
- [x] The pinned demo fixture reports a trade count greater than zero, and the fixture's exact config is named in the test
- [x] Re-running the fixture reproduces the stored report exactly, so later drift in scoring or replay fails this test

### Quality gates

- [x] All four project gates pass
- [x] The comparison function is pure and unit-tested against hand-written report dicts, requiring no backtest run
- [x] The pre-existing backtest test modules pass with no edits to their assertions

---

## Task 02-momentum-turn-and-extension-evidence

The trader can see two things the engine has never measured: **Stochastic RSI %K/%D** per
timeframe, and **how far price has travelled from its EMA20 in ATR terms** (signed: positive
above). Both appear in the scan's detailed view and in the AI analyst's evidence, behind their
own flags.

This slice changes **no decision**. It adds facts, and the next four tasks put them to work.
That is why it has no measured delta to report — adding data is not changing behaviour, and
pretending otherwise would corrupt the attribution for everything after it.

Two traps this task must avoid. The display and evidence additions need flags of their own,
or the requirement that "with all new options off the scan output is byte-identical" is broken
on day one. And the per-timeframe evidence builder is **shared with the momentum scanner**, so
an ungated addition silently changes `movers` output and its JSON too.

Note the indicator library returns Stochastic RSI on a **0-1** scale, not 0-100.

### Implementation steps

- [x] Add the `entry:` configuration block with this task's two flags, both defaulting off, validated with unknown-key rejection like the existing nested blocks
- [x] Add Stochastic RSI %K and %D to the per-timeframe feature bundle from the existing indicator library, stating the 0-1 scale
- [x] Add a signed EMA20 distance in ATR multiples to the same bundle
- [x] Leave both as non-finite in the feature bundle when history is too short, normalized to an explicit absence at the evidence boundary by the existing convention
- [x] Gate the analyst-evidence additions and the scan detailed-view additions on the two flags
- [x] Assert on a fixed fixture that every pre-existing feature value is unchanged

### Acceptance criteria

- [x] Every timeframe with sufficient history reports Stochastic RSI %K and %D, verified against a hand-computed expectation on a known series, on the 0-1 scale
- [x] A timeframe with too little history yields non-finite values in the feature bundle and an explicit absence in the brief
- [x] The EMA20 distance is positive above the EMA20 and negative below, and halves when ATR doubles for the same price gap
- [x] With both flags on, the analyst evidence and the scan detailed view carry all three values
- [x] With both flags off, the scan's rendered output and **both** scanners' briefs are unchanged, asserted against captured pre-task output
- [x] On a fixed candle fixture every pre-existing feature value is identical to its pre-task value, so the addition cannot move a coin's score
- [x] The task 01 demo fixture still reproduces exactly, confirming no decision changed

### Quality gates

- [x] All four project gates pass
- [x] The new values are computed in the existing pure indicator layer
- [x] The pre-existing indicator, scan and movers test modules pass with no edits to their assertions

---

## Task 03-stop-rewarding-extended-momentum

The engine stops scoring a coin highest exactly when it is most stretched. Today the momentum
category takes RSI and divides by 100, so RSI 90 earns 90% credit toward a long. Behind a flag
that becomes a plateau — full credit through a healthy band, tapering above it — so trend
strength stops doubling as entry timing.

The smallest behavioural change in the feature, and the one most likely to move the number on
its own. Its flag is separate from everything later so the delta is attributable to it alone.

### Implementation steps

- [x] Add this task's flag to the `entry:` block, defaulting off
- [x] Replace the linear RSI credit with a plateau curve when the flag is on, leaving the existing formula byte-for-byte when off
- [x] Apply the mirrored curve for short setups
- [x] Thread the flag through **both** the live analysis path and the backtest evaluation path, as a single settings value rather than another positional argument
- [x] Measure the flag on against a saved baseline over the iteration subset config, and record the delta in the feature's results table
- [x] Set the flag's documented default from that measurement, recording the numbers whichever way they fall

### Acceptance criteria

- [x] With the flag off, momentum credit on a fixed fixture is identical to its pre-task value for both directions
- [x] With the flag on, a long with RSI 90 does not receive more momentum credit than an otherwise identical long with RSI 60
- [x] With the flag on, a long inside the healthy band receives full momentum credit, and the mirrored assertion holds for a short with a low RSI
- [x] An unknown key inside the `entry:` block is rejected naming that key, and every flag in the block defaults off when the block is absent
- [x] The flag reaches both paths, verified by a live-path test observing the changed score **and** a backtest-path test observing a changed simulated outcome on a hand-built history
- [x] **[manual]** A measured comparison over the iteration config is recorded, reporting the signed change in trade count, win rate, expectancy, profit factor and drawdown
- [x] **[manual]** The flag's documented default matches what that measurement justifies

### Quality gates

- [x] All four project gates pass
- [x] The scoring function stays pure and is tested through hand-built features, needing no backtest for the unit assertions
- [x] With the flag off, the task 01 demo fixture reproduces exactly
- [x] The pre-existing scoring test modules pass with no edits to their assertions

---
## Task 04-breakout-retest-as-a-sequence

A breakout stops being a single price comparison. A new detector examines a window of candles
for the **sequence** — level broken, price returning, level holding or failing — and reports one
of four states, which then feeds the breakout credit.

The distinction that matters is the failure case: today a level broken and then lost still earns
breakout credit, because only the latest close against the latest pivot is examined. A retest
that failed is not a weaker breakout, it is evidence against the trade.

This comes before entry quality because the retest state is one of entry quality's components.
Building it after would mean reopening the entry scorer and re-running its measurement.

The four states are the decision this task encodes:

```
NONE               no break of the level within the window
BROKEN_NO_RETEST   level broken, price never returned to it
RETEST_HELD        broken, returned, held above/below  -> the entry we want
RETEST_FAILED      broken, returned, lost the level again
```

A level is a band, not a price, so both "returned" and "held" are judged within an ATR
tolerance. Closes beyond the level are decisive; wicks are not.

One plumbing constraint: the scoring layer receives features, structure and latest closes — it
has **no candle frames**. The retest state must be computed by each caller and passed in as a
new per-timeframe input rather than by importing candles into the scoring layer.

### Implementation steps

- [x] Add this task's flag and its lookback and tolerance tunables to the `entry:` block, with range validation
- [x] Add a pure detector over a candle window, one level, a direction and an ATR, returning one of the four states
- [x] Judge the return and the hold within the ATR tolerance; treat closes as decisive and wicks as not
- [x] Compute the state per timeframe in both callers and pass it into scoring as a new per-timeframe input
- [x] Feed the state into the breakout credit when the flag is on, so a failed retest earns no breakout credit
- [x] Carry the state into the analyst's evidence and the scan's detailed view
- [x] Thread the flag through both the live and backtest paths
- [ ] ~~Measure the flag on against a saved baseline over the iteration config and record the delta~~ *(skipped: the ~50-minute iteration-config run was stopped on the user's instruction as unnecessary now; the flag ships off, and task 07 measures every flag over the full window)*

### Acceptance criteria

- [x] Four hand-built sequences, one per state, are each classified correctly for a long, and the mirrored sequences are classified correctly for a short
- [x] A breach smaller than the tolerance is not a break, and a return to within the tolerance counts as a retest
- [x] A level breached by a wick but not a close does not produce a failed retest; one breached by a close does
- [x] A window shorter than the configured lookback yields the no-break state rather than raising
- [x] With the flag on, a failed retest receives no breakout credit, verified against an otherwise identical held retest; with the flag off, breakout credit on a fixed fixture is unchanged
- [x] The state appears in the analyst's evidence and the scan's detailed view, and out-of-range tunables are rejected naming the setting
- [ ] ~~**[manual]** A measured comparison over the iteration config is recorded, with the flag's documented default set from it~~ *(skipped: measurement run stopped on the user's instruction; default remains false — unmeasured, not measured-and-rejected — and task 07 owns the measurement)*

### Quality gates

- [x] All four project gates pass
- [x] The detector is pure over candles, level, direction and ATR, performs no I/O, and is added by name to the core-independence import list
- [x] With the flag off, the task 01 demo fixture reproduces exactly

---

## Task 05-entry-zones-with-honest-fills

The trade plan stops telling the trader to buy at whatever price is printing. Behind a flag it
proposes an **entry zone** with a trigger, marks itself **not yet actionable** when the current
price sits outside that zone, and computes reward-to-risk from the zone edge that would fill.

**This task must change the simulator, or its measurement is a lie.** The backtest currently
builds its entry at the reference close and walks subsequent bars from that assumed fill —
there is no pending-order concept anywhere. Moving the entry to a zone below the current close
would retroactively fill every long at a better price the market may never have printed, while
shrinking risk to roughly the zone-plus-buffer ATR distance. Both effects inflate R-multiples.
The delta would look excellent and mean nothing. So limit-fill semantics come first: a trade
exists only if a later bar trades into the zone within a bounded number of bars, and the
recorded fill is the zone edge touched.

A second trap: deriving the zone from the structural invalidation level puts it several ATR
below the close, so essentially every plan would be non-actionable and the trade list would be
permanently empty. The zone is derived from a pullback reference — the EMA20, or task 04's
retest band — not from the invalidation level. An acceptance criterion bounds the observed
actionable fraction so the degenerate outcome fails loudly instead of looking like a quiet
market.

The planner is also shared with the momentum scanner, so the zone must not appear there
unless the flag is explicitly extended to it.

### Implementation steps

- [x] Add this task's flag and the zone half-width and fill-window tunables to the `entry:` block, with range validation
- [x] Extend the trade plan with a zone, a trigger description and an actionable flag, populated only when the flag is on
- [x] Derive the zone from a pullback reference and ATR, not from the structural invalidation level
- [x] Extend the simulator with limit-fill semantics: a trade opens only when a subsequent bar trades into the zone within the fill window, recording the zone edge touched as the fill price; otherwise no trade
- [x] Define and implement what a non-actionable plan does in the backtest, and state it in the docs
- [x] Compute reward-to-risk from the fill edge rather than the latest close
- [x] Keep the single entry value populated for compatibility
- [x] Show the zone and actionable state in the scan output and the analyst's evidence
- [x] Thread the flag through both paths; ~~measure against a saved baseline over the iteration config~~ *(measurement skipped: the ~50-minute iteration runs were stopped as unnecessary at this stage; task 07 owns measurement — the threading itself is done and tested on both paths)*

### Acceptance criteria

- [x] A setup whose price never re-enters its zone within the fill window opens no simulated trade, verified on a hand-built history where the flag-off run does open one
- [x] The recorded fill price equals the zone edge touched, not the reference close, verified on a hand-built history
- [x] The zone brackets the pullback reference it was derived from, and widening the configured half-width widens it proportionally
- [x] Reward-to-risk is computed from the fill edge, verified by a case where that differs measurably from the latest-close ratio
- [x] A setup whose current price lies outside its zone is marked not actionable and one inside is marked actionable, and on the fixture sample the actionable fraction is above zero
- [x] With the flag off, the plan is identical in every field on a fixed fixture, the simulator behaves exactly as before, and the momentum scanner's plans are unchanged with the flag on
- [ ] ~~**[manual]** A measured comparison over the iteration config is recorded, with the flag's documented default set from it~~ *(skipped: measurement runs stopped on the user's instruction; default remains false — unmeasured, not measured-and-rejected. What is known without the run is recorded in results.md: zones-on takes 0 trades where flags-off takes 16 on the pinned fixture)*

### Quality gates

- [x] All four project gates pass
- [x] The planner and the simulator remain pure and deterministic, tested against hand-built candles
- [x] With the flag off, the task 01 demo fixture reproduces exactly
- [x] The pre-existing trade-planner, simulator, scan and movers test modules pass with no edits to their assertions

---

## Task 06-entry-quality-and-an-explicit-wait

The engine gains a second verdict. Alongside the 0-100 trend score it computes an **entry
score** and returns **ACTIONABLE** or **WAIT**, with the blockers behind a WAIT and the
condition that would clear it. The scan presents actionable and waiting setups as separate
groups, so a trade list and a watchlist stop being the same list.

**A WAIT must suppress the trade, or this task measures nothing.** The backtest decides whether
a moment produces a trade from three things only: a usable plan, the quality score clearing the
threshold, and the long-only filter. An entry verdict computed alongside that and read by
nobody yields an all-zero delta — and under this feature's own rule, a change that does not
improve the numbers ships off. The heart of the feature would be condemned by a wiring
omission. So the surfacing decision is additionally gated on the verdict, in both paths.

Entry components: extension and the Stochastic RSI turn from task 02, the retest state from
task 04, and reward-to-risk from task 05's fill edge. Each blocker names a measurable quantity
with its value — "extended 4.1 ATR above EMA20", not "looks stretched" — because a blocker the
trader cannot check is one they cannot learn from.

**What this does not do:** it cannot surface the motivating BOMEUSDT case. Entry quality
re-labels setups that already cleared the quality threshold; a setup scoring 51.7 never reaches
it. User story 4 is deferred, and changing what surfaces is a separate measured question.

### Implementation steps

- [x] Add this task's flag and the extension threshold tunable to the `entry:` block, with range validation
- [x] Add an entry score carrying a 0-100 total, an actionable-or-wait verdict, the blockers behind a wait, a trigger, and the per-component breakdown
- [x] Score extension against the configured ATR threshold, the momentum turn from the Stochastic RSI pair crossing toward the trade, the retest state, and reward-to-risk from the fill edge
- [x] Gate the surfacing decision on an actionable verdict in **both** the live path and the backtest evaluation path
- [x] Leave the existing quality score's meaning and weights untouched
- [x] Render actionable and waiting setups as separate groups and carry the verdict and blockers into the analyst's evidence
- [x] Thread the flag through both paths — *the measurement against the iteration config is deferred to task 07, per the decision already recorded for `entry.retest` and `entry.zones` in `results.md`; the flag ships off because it is unmeasured*

### Acceptance criteria

- [x] With the flag on, a moment whose entry verdict is WAIT opens no simulated trade, verified on a hand-built history where the flag-off run does open one
- [x] A setup extended beyond the configured threshold is reported WAIT and an otherwise identical setup within it is reported ACTIONABLE
- [x] Every WAIT carries at least one blocker and a trigger, and each blocker names a measurable quantity with its value
- [x] A pulled-back setup with momentum turning toward the trade scores higher on entry than an otherwise identical extended setup
- [x] With the flag off, no entry score is produced, the existing quality score is identical on a fixed fixture, and the surfacing decision is unchanged
- [x] The scan renders actionable and waiting setups as distinguishable groups, the analyst's evidence carries the verdict and blockers, and the momentum scanner's output is unchanged with the flag on
- [ ] ~~**[manual]** A measured comparison over the iteration config is recorded, with the flag's documented default set from it~~ *(skipped: ~100 minutes of compute for the control plus the variant, deferred to task 07 by the same standing decision recorded in `results.md` for `entry.retest` and `entry.zones`. The flag's documented default is `false`, set by the feature's rule for anything unmeasured. What **is** recorded is the deterministic-fixture effect — 16 trades flags-off vs 55 resolved quality-on, and why the count rises rather than falls.)*

### Quality gates

- [x] All four project gates pass
- [x] The entry scorer is pure and tested through hand-built features, structure and plans
- [x] With the flag off, the task 01 demo fixture reproduces exactly
- [x] Every module this task adds or touches remains free of `typer` and `rich`, and any new module is added by name to the core-independence import list

---

## Task 07-evidence-based-defaults

> **PARKED 2026-08-19 at the user's direction — not cancelled, not blocked.** Asked whether
> to finish the measurement, the user chose to run the scanner instead: the deliverable they
> want is a working daily driver, not a proof of edge. Every `entry.*` flag defaults **off**,
> so the shipped behaviour of `scan` and `backtest` is already correct without this task —
> what is missing is only the *evidence* for turning any of them on. Tasks 01-06 are complete
> and green. Pick this up only when the user asks for the numbers.

The feature stops being a set of switches and becomes a recommendation. Every flag is measured
individually and in combination over the full window and the full watchlist, the results are
published as one table, and each flag's shipped default is set by what the table shows rather
than by how persuasive the idea was.

This is what the feature exists to reach, and it will probably reject some of the preceding
work. A change that does not improve expectancy, profit factor or drawdown ships **off**, with
its numbers recorded beside the ones that helped. At a profit factor of 1.10, telling the two
apart is the entire point.

Budget the compute: baseline plus four flags plus one combination, at roughly 3.5 hours each,
is on the order of 21 hours. Run them unattended.

### Implementation steps

- [ ] Re-derive the baseline over `2022-01-01 → 2026-07-31` on the full watchlist with every flag off, and confirm it matches the recorded figures
- [ ] Measure each flag individually against that baseline over the same window and watchlist
- [ ] Measure the combination of the flags that individually helped
- [ ] Publish all results as one table in the repository, naming the config file, the watchlist size, the window and the fee and slippage settings used
- [ ] Set each flag's shipped default from its measured result and state the default beside its number
- [ ] Update the example configuration, the personal watchlist configuration and the README to match
- [ ] Record every change that did not help, with its numbers and the decision to ship it off

### Acceptance criteria

- [ ] **[manual]** The all-flags-off backtest over the recorded window and watchlist reproduces the recorded baseline figures
- [ ] **[manual]** The published table reports, for every flag individually, the signed change in trade count, win rate, expectancy, profit factor and max drawdown
- [ ] **[manual]** The table reports the same measures for the combination of flags that individually helped
- [ ] Loading the shipped example configuration yields a disabled flag for every flag the table does not record as an improvement, asserted by a test that reads the config
- [ ] The published table names the config file, watchlist size, window, fee and slippage settings, so the measurement can be repeated
- [ ] Every change that did not improve the measured result is documented with its numbers and ships off by default
- [ ] The README describes each flag, its measured effect and its default, and no documentation claims the engine is improved or profitable without the measured figures beside it

### Quality gates

- [ ] All four project gates pass
- [ ] Loading the shipped example configuration and the personal watchlist configuration both succeed with the new defaults
- [ ] The pre-existing test suite passes with no edits to any existing assertions

