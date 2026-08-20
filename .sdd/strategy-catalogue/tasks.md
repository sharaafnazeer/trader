# Strategy Catalogue — Tasks

Derived from `.sdd/strategy-catalogue/requirements.md` (2026-08-19). Tasks are vertical
slices in topological order; the ordinal is the implementation order. The rules being
implemented are recorded verbatim in `.sdd/strategy-catalogue/rules.md` — that file, including
its closed indicator list, is the specification, and it wins over any paraphrase here.

Project quality gates apply to every task: `uv run pytest`, `uv run ruff check`,
`uv run mypy src`, `uv run python -m compileall src`. So does one testing rule, stated once
rather than repeated: **no test may perform network I/O, read a real credential, sleep on the
wall clock, or write outside a temporary directory.**

## The governing rules

**The indicator set is closed.** EMA10, EMA21, EMA50, SMA200, Stochastic RSI, MACD, ATR,
volume, raw candles. Nothing else is computed, stored, rendered or sent to the model. No task
may add an indicator to make a condition easier to express — if a condition needs something
outside the list, that is a question for the trader, not a decision for the implementer.

**Removal is real removal.** Task 01 deletes the retired code rather than leaving it
unreachable behind a flag. A dormant scoring model is a thing future readers must understand
and future changes must keep compiling, for no benefit — and the trader asked for it gone.

**Both sides, every task.** 1A and 1B are one checklist read in two directions. A task that
implements a condition for the long side and defers the short side is not done — the mirror
lands in the same task, with its own test. Shorts reject from **resistance / the downtrend
line**, never from support.

**WAIT is the expected answer.** No task may introduce a tie-break, a fallback or a "best
available" that converts an unmet checklist into a trade. A scan where nothing is ready must
say nothing is ready.

**No performance claims.** Measurement is parked by explicit decision, so no task may add a
statement about win rate, expectancy or edge to output, documentation or comments. The
catalogue reports what it sees, never how well it has done.

## Two decisions taken before drafting

**Annex conflict — resolved by following the annex.** `trendline.fit` needs `(index, price)`
pairs, but `StructureState` carries pivot **prices only**; `structure._swing_indices` computes
bar positions and discards them. Task 06 extends `StructureState` with the index tuples,
defaulting to empty. One pivot detector rather than two.

**Blast radius of the closed indicator set.** Removing standalone RSI, rate of change, OBV,
Bollinger width, EMA20 and EMA200 removes the inputs to three of the old scoring model's eight
categories — trend, momentum and volume. The model therefore cannot survive and is retired in
task 01, along with `quality_threshold`, `surface()` and the whole `entry:` block (whose flags
operate on the removed RSI and EMA20). `retest.py` is kept: it uses swing levels and ATR, and
it is Strategy 3's starting point. The `movers` scanner is kept working: it reads the removed
rate of change once and computes it locally instead.

---

## Task 01-close-the-indicator-set

The engine stops computing anything the method does not use, and starts computing what it
does. **EMA10, EMA21 and SMA200** arrive; **EMA20, EMA200, standalone RSI, rate of change,
on-balance volume and its slope, and Bollinger band width** leave.

This is destructive by instruction and it goes first because nothing else can be built on a
feature bundle that is about to change shape. What leaves is not decoration: those fields were
the inputs to three of the eight scoring categories, so the **0-100 quality score is retired
here** — model, categories, weights, `surface()` and the quality threshold — rather than left
running on half its inputs under the same name and the same 0-100 range. The `entry:`
configuration block goes with it, since its flags read the removed RSI and EMA20.

The **direction rule moves onto the method's stack**: `EMA10 > EMA21 > EMA50 > SMA200` for an
uptrend, mirrored for a downtrend. Every direction decision the engine has ever made will
differ. That is the intent, not a side effect.

Two things are deliberately kept. `retest.py` uses swing levels and ATR, neither of them
removed, and it is where Strategy 3 will start. The `movers` scanner reads rate of change once
for its acceleration factor and already owns a period-return helper, so it computes the value
locally and its output is unchanged.

After this task the scan still runs: it lists each coin's direction, decided the method's way,
with its trade plan. It shows no score, because there no longer is one. The named setups
arrive in task 02.

### Implementation steps

- [x] Add EMA10, EMA21 and SMA200 to the per-timeframe features, using a simple average for the 200 rather than an exponential one
- [x] Remove EMA20, EMA200, RSI, rate of change, on-balance volume and its slope, Bollinger band width, and the EMA20-distance field from the feature bundle and everywhere they are read
- [x] Move the direction rule onto the method's stack, mirrored for the short side
- [x] Delete the scoring model, its categories, its weights, the surfacing helper and the quality threshold, together with the configuration keys that fed them
- [x] Delete the `entry:` configuration block and the entry-score machinery behind it, keeping the retest detector
- [x] Give the momentum scanner its own acceleration input so it stops reading the removed field
- [x] Reject a configuration naming any removed key by name, saying what replaced it
- [x] Repoint the analyst's candidate gate, which filtered on the retired score, so it selects on having a decided direction and keeps its per-run cap and per-direction reserve working
- [x] Update the scan and the analyst evidence to stop referring to the removed values and the retired score

### Acceptance criteria

- [x] The per-timeframe feature bundle contains exactly the method's indicators, asserted by enumerating its fields and failing on any name outside the closed list
- [x] No removed identifier appears anywhere in the package, asserted by a source-level search so a reintroduction is caught wherever it happens
- [x] A coin whose EMA10, EMA21, EMA50 and SMA200 are stacked in ascending order on the higher timeframes resolves LONG; the mirrored stack resolves SHORT; a mixed stack resolves no direction
- [x] A configuration naming a removed key — including the retired quality threshold and any `entry:` flag — is rejected with a message naming the replacement, rather than silently ignored
- [x] A scan produces no 0-100 score anywhere in its table, its JSON or its analyst evidence
- [x] The momentum scanner produces its ranked movers with an unchanged acceleration factor, asserted by its existing tests passing without modification
- [x] A timeframe with too little history for the 200-period average reports it as absent rather than as a number
- [x] The analyst still receives candidates, still respects its per-run cap and its per-direction reserve, and still returns a verdict with a confidence for each one

### Quality gates

- [x] All four project gates pass
- [x] The pre-existing momentum-scanner tests pass with no edits to any assertion about its scores
- [x] No module retains an import of, or a reference to, the deleted scoring model
- [x] Every module this task touches is free of `typer` and `rich`, and the core-independence import list no longer names any deleted module

---

## Task 02-the-two-question-verdict

The engine gains its first named setup and answers two questions separately instead of one:
*what is the trend?* and *is there a valid entry now?* The scan grows a Setups view reporting
trend, setup, entry status, decision and reason as distinct fields.

Three conditions make the slice real. **Stack** and **structure** decide the trend; **zone
position** decides the entry. The zone is the band between EMA21 and EMA50, and the condition
records how many ATR outside it price sits — which is what turns "extended" from an adjective
into a number, and what makes the motivating case return WAIT:

```
EMA10 > EMA21 > EMA50 > SMA200   OK      -> trend LONG
HH + HL                          OK
price 4.1 ATR above EMA21        FAIL    -> entry NOT_READY
                                         -> decision WAIT
```

Structure reuses the existing detector unchanged: it already classifies higher-highs-and-
higher-lows exactly as the method defines it, and re-implementing it would create two answers
to one question.

Reward-to-risk is handled here too, but **as a preference rather than a condition** — a
decision taken deliberately and recorded in the rules file. The planner already floors every
take-profit at the ratio, so 1:2 is guaranteed and cannot be violated; what is unknown is
whether that target is a real swing level or one the planner manufactured. That distinction is
*reported* and *ranks* setups, and never blocks one, because gating on it would reject any coin
at new highs — which has no overhead pivot by definition — and on the live watchlist would
refuse 38 of 42 planned coins.

The remaining four conditions arrive in tasks 03-06. Until then the checklist is short, and the
verdict must say how many conditions it actually evaluated rather than implying the whole method
was applied — an entry called READY on three of seven conditions is a claim the engine cannot
yet support.

### Implementation steps

- [x] Add the strategy module: the strategy identifiers, the entry status, a per-condition record carrying its own measured detail, and the verdict that composes them
- [x] Evaluate the stack, structure and zone-position conditions for both 1A and 1B
- [x] Record on each plan whether its take-profit is a structural pivot or was floored to reach the ratio, and show that distinction wherever a plan is displayed
- [x] Rank a setup with a structural target above an otherwise equal one with a manufactured target, without either blocking the entry
- [x] Derive the reason from the unmet conditions rather than composing it separately, so it cannot drift from the checklist
- [x] Report how many of the method's conditions were evaluated and how many were met, so a short checklist is visible as such and the closest setups can be ordered first
- [x] Add the `strategies:` configuration block with its thresholds, range validation and rejection of unknown keys by name
- [x] Render the Setups view — trend, setup, entry status, decision, reason — and state plainly when nothing is ready
- [x] Carry the verdict and the method's moving averages into the analyst's evidence
- [x] Add the new module by name to the core-independence import list

### Acceptance criteria

- [x] A coin with the ascending stack, higher highs and higher lows, and price inside the EMA21-EMA50 band is reported trend LONG, setup trend-pullback, entry READY, decision LONG
- [x] The same coin with price beyond the configured extension threshold above the band is reported entry NOT_READY, decision WAIT, with the reason naming the ATR distance and the band it must return to
- [x] The mirrored short case is reported trend SHORT with the same three outcomes, its reason referring to the band as resistance rather than support
- [x] A coin whose stack ordering fails is reported with no setup matched, rather than as a WAIT on a setup that does not apply
- [x] Every WAIT reason names at least one measured quantity with its value
- [x] A scan in which no coin reaches READY says so explicitly instead of rendering an empty table
- [x] Changing the extension threshold in configuration changes the verdict on an otherwise identical coin
- [x] A plan whose take-profit is a real swing level is marked structural; one that had to be pushed out to reach the ratio is marked manufactured, and both still report a ratio of at least the configured target
- [x] A coin whose target is manufactured is not blocked and can still reach entry READY, so a coin at new highs remains tradeable by the checklist
- [x] Between two setups meeting the same conditions, the one with a structural target is ranked first
- [x] The Setups view is ordered by conditions met, so a coin one condition short is read above a coin three short, and coins with no setup are last

### Quality gates

- [x] All four project gates pass
- [x] The strategy evaluator is pure — exercised through hand-built condition inputs with no candle frames constructed
- [x] No removed indicator is reintroduced, asserted by the source-level check from task 01 still passing
- [x] Every module this task adds or touches is free of `typer` and `rich`, and the new module is named in the core-independence import list

---

## Task 03-stochastic-rsi-turns-from-an-extreme

The checklist gains its momentum condition, deliberately stricter than reading a level. The
method requires an **event**: %K crossing %D in the trade's direction, and crossing it **at or
near an extreme**.

Both halves reject a different mistake. Without the cross, a coin that is merely oversold looks
like a signal — and a falling knife is oversold all the way down. Without the extreme, a
mid-range wobble qualifies, and mid-range wobbles are noise. The method says "reaches or
approaches oversold", so the threshold is configurable and "approaches" is honoured by allowing
the extreme to have been touched within a lookback rather than on the crossing bar exactly.

### Implementation steps

- [x] Derive the cross events from the Stochastic RSI pair, recording the direction of the cross and the extreme it came from
- [x] Expose how recently the cross occurred, so "just turned" is distinguishable from "turned twenty bars ago"
- [x] Add the momentum-turn condition to both checklists, requiring the cross direction to match the trade and the extreme to have been reached within the configured lookback
- [x] Make the oversold level, the overbought level and the cross lookback configurable with validated ranges
- [x] Carry the cross state and the values behind it into the analyst's evidence and the Setups view's reason

### Acceptance criteria

- [x] A coin meeting every task 02 condition whose %K has just crossed above %D from below the oversold level is reported entry READY, decision LONG
- [x] The same coin with %K deeply oversold but not yet crossed is WAIT, and the reason names the momentum turn with the current %K and %D values
- [x] A cross occurring in the middle of the range does not satisfy the condition, and the reason says the cross was not from an extreme
- [x] A cross older than the configured lookback does not satisfy the condition
- [x] The mirrored short case requires %K crossing below %D from above the overbought level
- [x] Changing the oversold threshold changes the condition's outcome on an otherwise identical coin

### Quality gates

- [x] All four project gates pass
- [x] Cross detection is asserted on a hand-built oscillator series, including the crossing bar and the bar after the lookback expires
- [x] No test constructs a Stochastic RSI value by monkey-patching the indicator layer
- [x] No removed indicator is reintroduced

---

## Task 04-macd-confirmation

The second momentum condition, with the same distinction: the method asks for a **bullish
crossover and/or a bullish position relative to zero**.

The "and/or" is in the method and is honoured rather than tidied away — either the crossover
event or the correct side of zero satisfies the condition, and the reason states which one did.
A crossover above the zero line is the strongest reading and is reported as such, so a trader
can tell it from a crossover deep in negative territory without opening the chart.

### Implementation steps

- [x] Derive the MACD crossover event and its recency alongside the line's side of the zero line
- [x] Add the MACD condition to both checklists, satisfied by a directional crossover within the lookback or by the correct side of zero
- [x] State in the condition's detail which of the two satisfied it, and note when both did
- [x] Carry the crossover state, its recency and the zero-line side into the analyst's evidence
- [x] Render the condition's outcome in the reason when it blocks the entry

### Acceptance criteria

- [x] A coin whose MACD line has just crossed above its signal satisfies the condition, and the detail names the crossover and the side of zero it happened on
- [x] A coin whose MACD has been above its signal for many bars reports no fresh crossover but still satisfies the condition on the zero-line reading when the line is above zero
- [x] A coin with MACD below both its signal and zero fails the condition for a long, and the reason names both readings with their values
- [x] The mirrored short case is satisfied by a bearish crossover or by the line sitting below zero
- [x] A coin meeting every condition from tasks 02-04 is reported entry READY, decision LONG

### Quality gates

- [x] All four project gates pass
- [x] Crossover recency is asserted on a hand-built series at the crossing bar, inside the lookback, and after it expires
- [x] No removed indicator is reintroduced
- [x] The strategy evaluator remains pure and free of candle-frame construction in its unit tests

---

## Task 05-the-reaction-candle

The checklist gains price-action confirmation: a **bullish reaction candle at the retracement
zone** — bullish engulfing, hammer or pin-bar rejection, or a morning-star reversal — and the
bearish mirrors for 1B.

This is the densest module in the feature and the one most likely to need correction, because
textbook pattern definitions vary and the trader's coaches may use tighter ones. It is
therefore one pure predicate per pattern over a handful of bars, thresholds configurable,
behaviour asserted on hand-built sequences. Getting a definition wrong should cost one constant
and one test, not a redesign.

The condition is **positional**: a hammer somewhere in the history is irrelevant; the method
wants the reaction *at the zone*. The pattern must appear on a recent bar while the zone
condition also holds, or it does not count.

### Implementation steps

- [x] Add the patterns module with one predicate per pattern — engulfing, hammer, shooting star, morning star, evening star — pure over the tail of a candle frame
- [x] Report every pattern present rather than the first match, so a bar that is both is not silently reduced to one
- [x] Add the reaction condition to both checklists, requiring a directionally-appropriate pattern on a recent bar while price is at the zone
- [x] Make the wick-to-body ratio and the recency window configurable with validated ranges
- [x] Carry the detected patterns into the analyst's evidence
- [x] Add the new module by name to the core-independence import list

### Acceptance criteria

- [x] Each of the five patterns is detected on a hand-built sequence built for it, and each such sequence yields that pattern and no other
- [x] An ordinary candle matching no definition yields no pattern rather than the nearest match
- [x] A candle whose body merely equals the previous body is not an engulfing, and a hammer with an upper wick larger than its body is not a hammer
- [x] A frame with fewer bars than a three-candle pattern needs returns no pattern rather than raising
- [x] A coin at the zone with a bullish engulfing satisfies the reaction condition; the same coin without the pattern is WAIT with the reaction named as unmet
- [x] A bullish pattern occurring while price is outside the zone does not satisfy the condition
- [x] The short side is satisfied only by bearish patterns

### Quality gates

- [x] All four project gates pass
- [x] Every pattern predicate is asserted both positively and negatively on hand-built bars
- [x] The patterns module is free of `typer` and `rich` and is named in the core-independence import list
- [x] No removed indicator is reintroduced

---

## Task 06-the-trendline

The last condition of the method: a **rising trendline** through recent swing lows for 1A, a
**falling trendline** through recent swing highs for 1B, and the test that price has returned
to it.

This task resolves the annex conflict recorded above. The existing pivot detector finds the
swings but discards their bar positions, and a line cannot be fitted to prices without knowing
when they occurred. The positions are added to the existing structure record rather than a
second detector being written, so there remains exactly one answer to "where are the swings".

A line through three points that are not actually collinear is a line the trader would never
draw. The fit therefore reports how well the pivots lie on it, and a fit below the configured
quality is **no trendline** rather than a weak one — an absent reading, never a fabricated one.

### Implementation steps

- [x] Extend the structure record with the bar positions of the detected swing highs and lows, defaulting to empty so existing constructions stay valid
- [x] Add the trendline module: fit a line through recent pivots, reporting slope, current level, touch count and fit quality
- [x] Return no line when there are too few pivots or the fit quality is below threshold
- [x] Add the trendline condition to both checklists, with price's distance from the line in ATR
- [x] Make the minimum touches, the fit-quality threshold and the proximity tolerance configurable with validated ranges
- [x] Carry the trendline and price's distance from it into the analyst's evidence

### Acceptance criteria

- [x] Ascending swing lows fit a line reported as rising, with its current level and touch count; descending swing highs fit one reported as falling
- [x] Perfectly collinear pivots report the maximum fit quality; scattered pivots fall below the threshold and yield no line
- [x] Fewer pivots than the configured minimum yields no line rather than an error
- [x] Price's distance from the line is expressed in ATR and scales as ATR changes for the same absolute gap
- [x] A coin in an uptrend whose price has returned to within tolerance of its rising trendline satisfies the condition; the same coin far above the line does not
- [x] A coin meeting every condition from tasks 02-06 is reported entry READY, decision LONG, with all seven conditions marked met
- [x] The mirrored short case is satisfied only by a falling line with price rallying back to it

### Quality gates

- [x] All four project gates pass
- [x] The fitter is asserted on hand-built pivot sets, including the collinear, scattered and too-few cases
- [x] The trendline module is free of `typer` and `rich` and is named in the core-independence import list
- [x] No removed indicator is reintroduced

---

## Task 07-the-analyst-evaluates-the-catalogue

The model stops improvising a framework and starts evaluating the trader's method. Its
instructions **name strategies 1A and 1B and state their conditions**, its evidence carries
every value those conditions are judged on, and its answer says which strategy it judged and
whether it considers the entry ready.

This is where the two readings meet. The detector's verdict and the analyst's are both shown,
side by side, and neither overrides the other. That is deliberate: a checklist can be
technically met in a context that is obviously wrong, and it can be one condition short of a
setup the model can see completing. Showing both is also the only way to find out later which
is worth listening to.

The evidence gains **raw candles for the decision timeframe** at a bounded bar count, so the
model can read price action the five pattern predicates do not cover. The count is configurable
because it is the largest single contributor to prompt size, and each run already reports its
token cost.

### Implementation steps

- [x] Replace the retired score floor with a conditions-met floor: a coin reaches the analyst when its checklist is within a configurable number of conditions of complete, keeping the per-run cap and the per-direction reserve
- [x] Order the actionable results by the analyst's confidence, falling back to conditions met when no analyst ran, so the list always has a defensible order
- [x] Extend the analyst's instructions to name both strategies, state each condition, and require a verdict to identify the strategy judged and its entry-status reading
- [x] Restate in the instructions that WAIT is the expected answer for a trend without an entry, and that no trade need be found
- [x] Extend the response schema with the strategy and entry-status fields, rejecting a verdict that omits them
- [x] Include the decision timeframe's recent candles in the evidence at a configurable bar count
- [x] Render the detector's verdict and the analyst's side by side, marking where they disagree
- [x] Keep the evidence strictly encodable with no non-finite values

### Acceptance criteria

- [x] The analyst's instructions name strategies 1A and 1B and state the conditions of each
- [x] The evidence carries every method indicator, the zone position, the trendline, the detected patterns, the Stochastic RSI cross state and the MACD crossover state, and nothing that was removed
- [x] The evidence carries the requested number of recent candles for the decision timeframe, and changing the configured count changes how many are sent
- [x] A model response omitting the strategy or entry-status field is rejected rather than accepted with a default
- [x] A response naming a different strategy from the detector's renders both readings
- [x] The evidence serializes under a strict encoder that forbids non-finite numbers
- [x] With no credential configured the detector's verdicts are still produced and displayed, ordered by conditions met
- [x] Each returned verdict carries an action and a 0-100 confidence, and the actionable list is ordered by that confidence
- [x] A coin whose checklist is further from complete than the configured floor does not reach the analyst, and lowering the floor admits it
- [x] The per-direction reserve still guarantees its share of long and short candidates when both exist

### Quality gates

- [x] All four project gates pass
- [x] The analyst is exercised through an injected fake completion — no test issues a model request
- [x] The momentum scanner's rendered output is unchanged
- [x] No removed indicator is reintroduced

---

## Task 08-defaults-documentation-and-the-closed-set-guard

The feature becomes something the trader can run and reason about. Every threshold gets a
documented default traceable to the method, the shipped configurations are updated, the README
describes each strategy and each knob, and the closed indicator set gets a permanent guard.

That guard is the load-bearing item. "Only these indicators" is an instruction that decays the
moment someone adds a field to solve a local problem, so it is enforced by a test that
enumerates the feature bundle and fails on any unexpected name — not by a comment asking
future readers to be careful.

Documentation carries one hard constraint: **no claim about win rate, expectancy or edge
appears anywhere.** These are the strategies the trader was taught; whether they make money is
unmeasured, and is stated as unmeasured.

### Implementation steps

- [x] Set every threshold's shipped default and record beside each one what it means in the method's terms
- [x] Update the example configuration and the personal watchlist configuration, removing the retired blocks and adding the new one with its comments
- [x] Document each strategy in the README — its conditions, its thresholds, and how to read the four-field output
- [x] Document the closed indicator set and state that additions require an explicit request
- [x] State in the documentation that the strategies are unmeasured, with no claim about profitability
- [x] Verify a full scan against the real watchlist produces the four-field output for every analysed coin

### Acceptance criteria

- [x] Loading the shipped example configuration and the personal watchlist configuration both succeed and yield the documented defaults, asserted by a test that reads the files
- [x] Neither shipped configuration retains a retired key, asserted by loading them rather than by inspection
- [x] Every configurable threshold appears in the README with its default and its meaning
- [x] The README lists the closed indicator set and states that additions require a request
- [x] No documentation, comment or rendered output claims the strategies are profitable, improved or validated
- [x] A scan over the real watchlist reports trend, setup, entry status and decision for every analysed coin, and states plainly when none is ready

### Quality gates

- [x] All four project gates pass
- [x] `uv run trader scan --config mywatch.yaml --dry-run-ai` completes without error and renders the Setups view
- [x] The feature-bundle enumeration test fails if any field outside the closed list is added, verified by adding one temporarily and observing the failure
- [x] The full test suite passes with no new failures beyond the three pre-existing `--help` rendering failures
