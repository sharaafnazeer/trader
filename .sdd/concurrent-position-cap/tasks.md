# Concurrent Position Cap — Tasks

Derived from `.sdd/concurrent-position-cap/requirements.md` (2026-08-19). Tasks are vertical
slices in topological order; the ordinal is the implementation order.

Project quality gates apply to every task: `uv run pytest`, `uv run ruff check`,
`uv run mypy src`, `uv run python -m compileall src`. One testing rule applies throughout and
is stated once: **no test may perform network I/O, read a real credential, sleep on the wall
clock, or write outside a temporary directory.**

## Governing constraints

**Both caps default to unlimited (`null`)**, and with them unset the backtest must reproduce
the recorded baseline exactly. Every cap value's effect is measured before any default changes.

**The subset shortcut dies here.** While coins are simulated independently, any subset's
metrics can be recomputed by filtering the trade log — which is how 2,000 coin subsets were
tested in minutes. Once a trade's existence depends on what else was open, that is gone: every
cap value needs a full replay. Budget ~48 minutes per run on the 20-coin iteration config.

**Replays are single-threaded and the cache is warm**, so runs are independent processes each
pinning one core. On a 14-core machine a five-value cap sweep runs in parallel in roughly the
time of one run, not five. Measurement steps should launch runs concurrently.

Reference figures, `bench-slow.yaml` (20 coins, 2022-01-01 → 2026-07-31, uncapped):
**563 trades, 39.1% win rate, +0.1565R, PF 1.251, 40.9% max drawdown.**
Full 87-coin watchlist, uncapped: **1,362 trades, 35.8%, +0.0646R, PF 1.099, 75.1% drawdown.**

---

## Task 01-chronological-replay-equivalence

The backtest gains the ability to reason about a portfolio, by advancing through time
chronologically instead of walking one coin from start to finish before touching the next.
Nothing about the results changes — and proving that is the entire deliverable.

This has to come first and has to be isolated. Today each coin is replayed in complete
isolation and merged into a flat list, so at the moment a trade would open there is nothing
that knows what else is open; a cap cannot be attached to that shape. But if the restructure
and the cap land together, a changed result is ambiguous — cap effect, or refactor bug? The
equivalence proof is what removes that ambiguity for every later task.

**The change is smaller than it sounds.** The per-coin replay already runs in two distinct
phases: evaluate every moment point-in-time, then apply the one-position / enter-on-transition
lifecycle over those evaluations. Only the *second* phase needs to move. Evaluation stays
per-coin and untouched — it is genuinely independent, since each coin slices its own frames
against its own reference closes. What becomes portfolio-level is the lifecycle walk, which
today holds two variables per coin (the previous surfaced state, and the timestamp the coin
becomes flat again) inside a per-coin loop and must instead hold them per coin across a merged
timeline.

Two properties of the existing code make this tractable and are worth relying on explicitly:
the simulator resolves a trade in one call and **returns its exit time**, so the moment a slot
frees is known at admission; and the equity curve **sorts by entry time** before computing, so
a report does not depend on the order outcomes happen to be produced in.

The existing per-coin path is **kept**, not replaced, precisely so the two can be compared.

### Implementation steps

- [ ] Build a merged, ordered timeline of decision points from the union of every coin's reference-timeframe closes, since coins with different history start at different times
- [ ] Keep per-coin evaluation exactly as it is, and move only the lifecycle walk to portfolio level
- [ ] Hold the per-coin lifecycle state — previous surfaced flag, and the timestamp the coin becomes flat — keyed by coin across the merged timeline, preserving one open trade per coin and entry only on a fresh transition
- [ ] Keep the per-coin replay in place as the comparison reference
- [ ] Make the chronological path the one the backtest command uses
- [ ] Add an equivalence test comparing both replays element-wise over the shared deterministic fixture

### Acceptance criteria

- [ ] Both replays over the shared fixture produce the same **set** of trades — compared after sorting by entry time then symbol, on symbol, direction, entry time, exit time, result and R-multiple. They must not be compared in production order: the chronological replay emits trades in time order while the per-coin replay groups them by coin, so an element-wise comparison would fail on ordering alone even when every trade is identical
- [ ] The **summarized reports** of both replays are identical on trade count, win rate, expectancy, profit factor and max drawdown — the stronger claim, and achievable because the equity curve sorts by entry time before computing
- [ ] Both replays over a second fixture in which several coins qualify at the same moment produce the same set of trades, so simultaneous qualification is covered rather than only staggered entries
- [ ] The chronological replay preserves one-open-trade-per-coin, verified on a fixture where a coin re-qualifies while its own trade is still open
- [ ] A backtest over the demo fixture reproduces the pinned report exactly
- [ ] A full-window run on the 20-coin iteration config reproduces 563 trades, 39.1% win rate, +0.1565R, PF 1.251 and 40.9% drawdown **[manual]**
- [ ] Coins with no history, and coins whose data starts mid-window, are handled identically by both replays

### Quality gates

- [ ] All four project gates pass
- [ ] The timeline builder is pure over candle frames and unit-tested without running a backtest
- [ ] The pre-existing backtester, metrics and CLI-backtest test modules pass with no edits to their assertions

---

## Task 02-total-cap-with-its-honest-cost

The trader can say "never hold more than N positions at once", and the backtest models it
truthfully — including what it cost. When more setups qualify at a moment than there are free
slots, the best are admitted and the rest declined; when positions close, slots free up.

The report does not just claim a smaller drawdown. It states how many setups the cap declined,
how many positions were open over time, and — by simulating each declined setup on its own —
**how many of the declined would have won**. Reporting a halved drawdown without the winners it
turned down would be advocacy, not measurement.

That figure answers exactly one question: *"would this individual setup have won if taken?"*.
It is **not** a re-simulation of the portfolio without the cap — admitting a declined trade
would have occupied a slot and displaced something else, and chasing that counterfactual to its
conclusion is just the uncapped run, which is already the baseline. The report must word it
that way so the number is not over-read.

The admission decision is a pure function over the current portfolio and the moment's
candidates, so slot arithmetic is asserted directly rather than inferred from a trade log. Its
ranking reuses the AI analyst's existing rule — engine total descending, ties broken by symbol
— because two different answers to "which setups matter most" in one codebase is a bug waiting
to happen.

```
admit(state, candidates, max_total, max_per_direction)
    -> (admitted, declined)          # declined carries a reason: total_cap | direction_cap
```

### Implementation steps

- [ ] Add a `portfolio:` configuration block with a total-open limit defaulting to unlimited, rejecting zero or negative values by name
- [ ] Add a pure portfolio value tracking open positions by direction, with slot accounting on open and close
- [ ] Add a pure admission function ranking candidates by engine total descending with symbol tie-break, admitting up to the free slots and declining the rest with a reason
- [ ] Wire admission into the chronological replay so declined setups open no trade
- [ ] Record declined setups and simulate them counterfactually to determine how many would have won
- [ ] Extend the report with the declined count, the distribution of concurrently-open positions, and the declined-winner count, all omitted when no cap is set
- [ ] Measure a range of cap values against the uncapped baseline on the iteration config, launching the runs in parallel, and record the deltas

### Acceptance criteria

- [ ] With no cap set, the backtest reproduces the task 01 equivalence result and the rendered report contains none of the three new fields
- [ ] Given five qualifying setups and three free slots, exactly the three highest-scoring are admitted and two are declined with reason `total_cap`
- [ ] A full book admits nothing; closing one position admits exactly one more; a cap of one produces a trade log in which no two trades overlap in time
- [ ] Two candidates with identical scores competing for one slot resolve deterministically across repeated runs
- [ ] The concurrency distribution sums to the number of decision points, and the declined-winner count includes only declined setups whose counterfactual outcome was a win
- [ ] **[manual]** A measured comparison across at least four cap values is recorded, reporting trade count, win rate, expectancy, profit factor and drawdown for each, plus the declined and declined-winner counts

### Quality gates

- [ ] All four project gates pass
- [ ] The portfolio value and the admission function are pure, with no I/O, and are tested without running a backtest
- [ ] The new module imports neither `typer` nor `rich` and is added by name to the core-independence import list
- [ ] With no cap set, the demo fixture reproduces its pinned report exactly

---

## Task 03-per-direction-cap

The trader can limit positions **in the same direction**, which is the limit that targets the
actual failure mode. A live scan surfaced 44 shorts against 7 longs; those are not 44
independent bets but one bet sized 44 times, and that is what a 75% drawdown is made of. A
book of three longs and three shorts carries far less correlated risk than six shorts, and only
a per-direction limit can tell them apart.

This is separate from the total cap so its effect is attributable. It is plausible that the
per-direction limit does most of the work and the total limit adds little — or the reverse —
and one measurement cannot answer that if both land together.

### Implementation steps

- [ ] Add a per-direction limit to the `portfolio:` block, defaulting to unlimited and rejecting zero or negative values by name
- [ ] Extend the admission function to enforce it alongside the total limit, declining with reason `direction_cap`
- [ ] Ensure the tighter of the two limits binds when both are set
- [ ] Report declined counts split by reason, so total-cap and direction-cap declines are distinguishable
- [ ] Measure the per-direction cap alone, and in combination with the total cap, against the uncapped baseline

### Acceptance criteria

- [ ] With a per-direction limit of two and five qualifying shorts, at most two shorts open regardless of a looser total limit
- [ ] With two shorts open and a per-direction limit of two, a qualifying long still opens — the limit does not block the opposite direction
- [ ] With both limits set, whichever is tighter at that moment is the one that binds, verified for a case where each is tighter in turn
- [ ] Declined setups are reported split by reason, and the split sums to the total declined count
- [ ] With the limit unset, admission behaves exactly as in task 02, verified on the same fixtures
- [ ] **[manual]** A measured comparison of the per-direction cap alone and combined with the total cap is recorded against the uncapped baseline

### Quality gates

- [ ] All four project gates pass
- [ ] The admission function remains pure and its per-direction behaviour is tested without a backtest
- [ ] With both limits unset, the demo fixture reproduces its pinned report exactly

---

## Task 04-live-advisory-and-analyst-slots

The trader sees the cap at the moment of deciding. A scan states how many of today's surfaced
setups fit within the configured cap and which would be declined, and the AI analyst is told
how many slots are free so it can weigh a marginal setup against the cost of filling the last
one.

**Advisory only, and it says so.** The application tracks no open positions, so the count
assumes a flat book. Presenting it as though the app knew what the trader holds would be worse
than not showing it — a trader already carrying five positions must not read "3 slots free" as
fact.

### Implementation steps

- [ ] Render, after the setups table, how many surfaced setups fit the configured cap and which would be declined with the reason
- [ ] State plainly in that output that it assumes no open positions
- [ ] Carry the free-slot count into the AI analyst's market context
- [ ] Show nothing new when no cap is configured
- [ ] Leave the momentum scanner untouched
- [ ] Document the advisory nature and both settings in the README

### Acceptance criteria

- [ ] With a cap configured and more surfaced setups than slots, the scan names which setups fit and which would be declined, with the reason
- [ ] That output states that it assumes a flat book
- [ ] With the analyst enabled, its evidence carries the free-slot count
- [ ] With no cap configured, the scan output and the analyst evidence are unchanged, asserted against captured pre-task output
- [ ] The momentum scanner's output is unchanged with a cap configured
- [ ] The README documents both settings, their measured effects, and that live enforcement is advisory

### Quality gates

- [ ] All four project gates pass
- [ ] The rendering is driven by the same pure admission function the backtest uses, not a reimplementation
- [ ] The pre-existing scan, movers and brief test modules pass with no edits to their assertions

---

## Task 05-evidence-based-defaults

The caps stop being switches and become a recommendation. A range of values is measured on the
full watchlist over the full window, published as one table stating drawdown **and** the
expectancy it cost, and each shipped default is set by that table.

This is the task the feature exists to reach, and it may reject the whole idea. Coin selection,
faster timeframes and the RSI plateau were each measured and shipped off; a cap that guts
expectancy joins them, with its numbers recorded beside theirs. The prediction under test is
specific: drawdown should fall faster than expectancy, and if it does not, capping is not the
answer.

Runs are independent single-threaded processes, so the whole matrix can be launched in
parallel and completed in roughly the time of one run rather than the sum.

### Implementation steps

- [ ] Re-derive the uncapped baseline on the full watchlist and window and confirm it matches the recorded figures
- [ ] Measure a range of total caps and per-direction caps, and the best combination, launching runs in parallel
- [ ] Publish the results in the feature's results file, stating window, watchlist size, and cost settings
- [ ] For every capped configuration, publish the drawdown reduction beside the expectancy cost and the declined-winner count
- [ ] Set each shipped default from the table and state the default beside its number
- [ ] Update the example configuration, the personal watchlist configuration and the README to match
- [ ] Record any cap value that made results worse, with its numbers

### Acceptance criteria

- [ ] **[manual]** The uncapped full-watchlist backtest reproduces 1,362 trades, 35.8% win rate, +0.0646R, PF 1.099 and 75.1% drawdown
- [ ] **[manual]** The published table reports, for each measured cap value, the trade count, win rate, expectancy, profit factor, drawdown, declined count and declined-winner count
- [ ] Every cap default in the shipped example configuration matches what the table justifies, asserted by a test that reads the configuration
- [ ] The published table names the window, watchlist size, fee and slippage settings, so the measurement can be repeated
- [ ] Any cap value that worsened the measured result is documented with its numbers rather than omitted
- [ ] No documentation claims a drawdown improvement without the expectancy cost stated beside it

### Quality gates

- [ ] All four project gates pass
- [ ] Loading the shipped example configuration and the personal watchlist configuration both succeed with the new defaults
- [ ] The pre-existing test suite passes with no edits to any existing assertions
