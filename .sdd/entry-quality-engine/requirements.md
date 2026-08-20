# Entry-Quality Engine

## Problem Statement

The engine answers one question — *"is this trend confirmed?"* — and then presents the
answer as though it were a different question: *"should I take this trade now?"* Those come
apart badly, and the gap is where money is lost.

Three concrete symptoms, all measured on this codebase:

1. **It tells the trader to buy at the current price, whatever that price is.** The trade
   plan's entry is the latest close, unconditionally. On a scan that surfaced 44 short
   setups against 7 longs, every one of those shorts said "sell here" with no test of
   whether price had already fallen a long way. If a coin has just dropped 12% in a
   session, "sell at the latest close" is the worst available instruction.
2. **It rewards chasing.** The momentum score takes RSI and divides by 100, so an RSI of 90
   earns 90% credit toward a long. The more extended a move is, the better the engine
   scores it — which is precisely backwards for entry timing, however useful it is as a
   read on trend strength.
3. **It cannot say "right idea, wrong moment."** Direction is decided on the higher
   timeframes and then a single 0-100 number is asked to carry both conviction *and*
   timing. A textbook pullback entry — higher-timeframe trend intact, price retraced to
   support on quiet volume — scores in the low 50s, because "retraced" and "quiet volume"
   are each penalised. The setups a trader most wants surfaced are the ones the score
   pushes furthest down.

A worked example from a live run: BOMEUSDT resolved LONG with a clean daily EMA stack
(20>50>200), daily RSI 63.5, bullish daily structure, price pulled back to near the 4h
swing low, and a 2:1 plan. It scored **51.7** — ranked 42nd of 51 — because volume sat at
3-44% of average and price was 17% below the daily high. Those deductions describe a
pullback correctly and then treat it as a defect.

Two capabilities are also simply absent: there is no Stochastic RSI (so "trend is up but
momentum is hooking down" is inexpressible), and no notion of a breakout *retest* (a
sequence — level broken, price returns, level holds, buyers resume — rather than a single
price comparison).

Underneath all of this sits the constraint that governs the whole feature: the engine's
only honest performance figure is **+0.065R per trade, profit factor 1.10, 35.8% win rate,
75.1% maximum drawdown over 1,362 trades (2022-01-01 → 2026-07-31)**. It is thin and not
tradeable as it stands. Any change that cannot be measured against that number is a change
of unknown value, and this problem statement is not short of plausible-sounding ideas.

## Solution

Split the single score into **trend quality** and **entry quality**, and let the engine
return *wait* as a first-class answer rather than only *long*, *short* or *nothing*.

Trend quality keeps doing what it already does well: higher-timeframe direction, EMA
stacking, market structure, BTC regime. Entry quality is new and asks a different question
— is price extended away from its moving averages, is short-term momentum turning back
toward the trade, has a broken level been retested and held, is the reward-to-risk from a
*sensible* entry still worth taking? A setup with strong trend and poor entry surfaces as
**WAIT** with the condition that would change it, instead of being buried at rank 42 or
presented as a plan to execute at market.

The trade plan stops naming a single entry price. It proposes an **entry zone** with a
trigger, so "the level to buy is 0.00076-0.00078 if it holds" is expressible, and a plan
whose zone is far from current price is honestly labelled as not-yet-actionable rather than
silently becoming a market order at whatever price happens to be printing.

Two indicators are added because the strategy genuinely needs them: **Stochastic RSI**, for
the "momentum turning inside an intact trend" read that ordinary RSI cannot give, and
**breakout-retest detection**, which examines a window of candles for the sequence rather
than comparing one close to one level.

Every one of these changes is placed behind configuration so it can be switched off, and
each is measured by re-running the existing backtester over the same 2022-2026 window and
comparing against the recorded baseline. A change that does not improve expectancy,
profit factor or drawdown is reported as such and kept off by default. The deliverable is
not "a better engine" on assertion — it is a table of measured deltas, and whichever
subset of changes that table justifies.

## User Stories

1. As a trader, I want the engine to distinguish trend quality from entry quality, so that a good idea at a bad price is not scored the same as a good idea at a good price.
2. As a trader, I want an explicit WAIT verdict when the trend is sound but the entry is poor, so that I know to watch a coin rather than either trading it or ignoring it.
3. As a trader, I want to be told what would turn a WAIT into an entry, so that I know what I am waiting for.
4. As a trader, I want setups I would recognise as textbook pullbacks to surface, so that the tool shows me the entries I actually want rather than only fully-extended moves.
5. As a trader, I want the engine to stop rewarding extended momentum as if it were entry quality, so that a coin is not ranked highest exactly when it is most stretched.
6. As a trader, I want a measure of how far price has travelled from its moving averages in volatility terms, so that "too late" is quantified rather than guessed.
7. As a trader, I want Stochastic RSI computed per timeframe, so that momentum hooking up from oversold inside an intact uptrend is visible to the engine.
8. As a trader, I want a breakout to be recognised as a sequence — level broken, price returns, level holds, buyers resume — so that a retest entry is distinguishable from a single candle poking through a level.
9. As a trader, I want a failed retest to be recognised too, so that a level breaking and then losing that level is not scored as a breakout.
10. As a trader, I want the trade plan to propose an entry zone rather than a single price, so that I am not told to buy at whatever the market happens to be printing.
11. As a trader, I want a plan whose entry zone is far from current price to be labelled not-yet-actionable, so that I do not treat a limit idea as a market order.
12. As a trader, I want reward-to-risk computed from the proposed entry zone rather than from the latest close, so that the ratio reflects the trade I would actually take.
13. As a trader, I want each new behaviour to be individually switchable in configuration, so that I can measure its effect instead of taking it on faith.
14. As a trader, I want every change measured against the recorded baseline over the same window, so that I can see whether it helped, hurt, or did nothing.
15. As a trader, I want the measured comparison reported as a table of deltas in expectancy, profit factor, win rate and drawdown, so that I can decide what to keep on evidence.
16. As a trader, I want a change that does not improve the measured result to stay off by default, so that the engine does not accumulate untested complexity.
17. As a trader, I want the baseline reproducible on demand, so that a later regression in the engine is detectable.
18. As a trader, I want the existing backtest command to work unchanged when every new option is off, so that I keep a trustworthy reference point.
19. As a trader, I want the new per-timeframe values included in the AI analyst's evidence, so that the model reasons from the same facts the engine now has.
20. As a trader, I want the entry-quality verdict visible in the scan output, so that I can see at a glance which setups are ready and which are waiting.
21. As a trader, I want the WAIT setups listed separately from the actionable ones, so that a watchlist and a trade list are not the same list.
22. As a maintainer, I want each new indicator computed in the existing pure indicator layer, so that it is testable without network access and reusable by both scanners.
23. As a maintainer, I want the retest detector to be a pure function over candles, so that its behaviour on a known sequence can be asserted exactly.
24. As a maintainer, I want the new analysis modules free of terminal-rendering dependencies, consistent with the rest of the codebase.
25. As a trader, I want the momentum scanner to keep working unchanged unless a change is explicitly extended to it, so that improving the trend engine does not silently alter the other one.

## User Acceptance Tests

1. Given a coin whose higher timeframes are cleanly bullish but whose price is far above its moving averages in ATR terms, when it is analysed, then it is reported as WAIT rather than as an actionable long.
2. Given that same coin, when the WAIT verdict is displayed, then it states the condition that would make it actionable.
3. Given a coin whose higher timeframes are bullish and whose price has retraced toward support with short-term momentum turning up, when it is analysed, then its entry quality is scored higher than the extended coin in test 1.
4. Given a coin with an RSI of 90 and a coin with an RSI of 60, both in an otherwise identical bullish setup, when both are scored, then the RSI-90 coin does not receive a higher entry-quality score than the RSI-60 coin.
5. Given any analysed coin, when its per-timeframe values are inspected, then a Stochastic RSI %K and %D value is present for each timeframe with sufficient history.
6. Given a timeframe with too little history for Stochastic RSI, when it is analysed, then the value is reported as absent rather than as a misleading number.
7. Given a candle sequence in which price closes above a prior resistance level, returns to that level, holds above it, and then advances, when the sequence is analysed, then a successful breakout retest is reported.
8. Given a candle sequence in which price closes above a prior resistance level, returns, and then closes decisively back below it, when the sequence is analysed, then a failed retest is reported and no breakout credit is given.
9. Given a candle sequence in which price closes above a level and continues upward without returning, when it is analysed, then it is reported as a breakout without a retest, distinguishable from both of the above.
10. Given a mirrored sequence below a support level, when it is analysed, then the equivalent short-side retest outcomes are reported.
11. Given a surfaced setup, when its trade plan is inspected, then it carries an entry zone with a lower and upper bound rather than a single entry price.
12. Given a setup whose entry zone lies far from the current price, when its plan is displayed, then it is marked as not yet actionable.
13. Given a setup whose entry zone contains the current price, when its plan is displayed, then it is marked as actionable.
14. Given a trade plan with an entry zone, when its reward-to-risk is inspected, then the ratio is computed from the zone rather than from the latest close.
15. Given every new option switched off in configuration, when a backtest is run over a fixed window, then the reported expectancy, profit factor, win rate and drawdown match the recorded baseline exactly.
16. Given every new option switched off, when a scan is run, then its output is unchanged from the pre-existing behaviour.
17. Given a single new option switched on, when a backtest is run over the same window, then a comparison against the baseline is produced showing the change in expectancy, profit factor, win rate and drawdown.
18. Given a comparison in which a change does not improve any of those measures, when the feature is delivered, then that option's documented default is off and the measured result is recorded.
19. Given the AI analyst is enabled, when its evidence is inspected, then the Stochastic RSI values, the extension measure and the retest state appear in it.
20. Given a scan with entry quality enabled, when the results are displayed, then actionable setups and waiting setups are presented as distinguishable groups.
21. Given the momentum scanner, when it is run with the trend engine's new options enabled, then its own output is unchanged unless the change was explicitly extended to it.
22. Given the recorded baseline figures, when the baseline backtest is re-run at any later date over the same window and cached data, then it reproduces the same numbers.

## Definition of Done

- All user acceptance tests pass.
- Every new behaviour is individually switchable, and every one defaults to off.
- With all new options off, the backtest reproduces the recorded baseline exactly, and the scan output is byte-identical to current behaviour.
- A measured comparison table exists covering each change individually and the combination of those that helped, over the 2022-01-01 → 2026-07-31 window on the existing cache.
- Each change's documented default reflects its measured result, not its intuitive appeal.
- Any change that did not improve the measured result is documented as such, with its numbers, rather than quietly removed or quietly kept.
- No claim that the engine is profitable or improved appears anywhere without the measured figures beside it.
- The new analysis modules import no terminal-rendering libraries.
- The full pre-existing test suite passes unchanged, and the project's quality gates (tests, lint, type check, compile) are green.
- User-facing documentation explains each new option, its measured effect, and its default.

## Out of Scope

- **Order execution.** Unchanged: this remains advisory.
- **Giving the language model authority over the decision.** This feature improves the deterministic engine; whether the analyst should decide instead is a separate question, answerable only once there is a measured engine to compare it against and a populated decision log.
- **Adding SMA.** Deliberately excluded until evidence shows it carries information the EMA stack and structure detector do not. More indicators is not the goal.
- **Support/resistance zones from clustered pivots.** A real improvement over single-price pivots, but a larger change; the retest detector here works from existing swing levels.
- **Backtesting the momentum scanner.** Still deferred, still unproven.
- **Re-tuning category weights or the quality threshold.** Out of scope so that measured deltas are attributable to the changes themselves.
- **Streaming market data.** Polling is retained.
- **Changing the direction rule.** Which timeframes decide direction, and whether lower timeframes should dilute scoring, is a separate measured question.

## Further Notes

The ordering principle for this feature is that every change is backtestable, and that is
the whole reason for choosing it over handing the decision to a language model. An
LLM-as-primary-trader cannot be replayed over four years of history at any sane cost, so it
can only be forward-tested, which takes months before the sample means anything. These
changes can be answered this week. Do the provable things first.

Expect some of them to fail. The purpose of the comparison table is to find out which,
and a feature that ships three improvements and honestly reports two duds is a better
outcome than one that ships five and cannot say which mattered.

---

## Technical Annex
> Written against codebase as of: 2026-08-19

### Architectural Decisions

**Everything is flag-gated, every flag defaults off.** This is the load-bearing decision.
Each change lands as a config boolean under a new `entry:` block; with all of them false the
scoring, planning and backtest paths execute exactly the code they execute today. That is
what makes "reproduces the baseline exactly" a testable assertion rather than a hope, and it
is what allows one change to be measured at a time.

```yaml
entry:
  quality: false            # compute an entry score and allow a WAIT verdict
  stoch_rsi: false          # compute Stochastic RSI per timeframe
  retest: false             # sequence-based breakout/retest detection
  extension_guard: false    # penalise price extended from its EMAs in ATR terms
  zones: false              # plan an entry zone + trigger instead of a single close
  # tunables, only consulted when the flag above is on
  max_extension_atr: 3.0    # ATR multiples above/below EMA20 that count as extended
  retest_lookback: 30       # candles examined for the break -> return -> hold sequence
  zone_atr: 0.5             # half-width of the entry zone, in ATR
```

**`indicators.py` gains fields, not a new module.** `TimeframeFeatures` grows
`stoch_rsi_k`, `stoch_rsi_d` and `ema20_distance_atr` (signed: positive = above EMA20).
`ta.momentum.StochRSIIndicator` supplies the first two; the third is
`(close - ema20) / atr`, which is the extension measure in the only units comparable across
coins. Existing fields and their computation are untouched, so a coin's current score cannot
move because a new field was added. Insufficient history yields `NaN`, normalized to `None`
at the brief boundary exactly as the existing fields are.

**New pure module `retest.py`.** A sequence detector, not a comparison:

```python
class RetestState(StrEnum):
    NONE = "none"                 # no break of the level in the window
    BROKEN_NO_RETEST = "broken"   # level broken, price never returned to it
    RETEST_HELD = "held"          # broken, returned, held -> the entry we want
    RETEST_FAILED = "failed"      # broken, returned, lost the level again

def detect(candles, level, direction, *, lookback=30, tolerance_atr=0.25) -> RetestState
```

Pure over a `Candles` frame plus one level, so a hand-built sequence asserts an exact
outcome. `tolerance_atr` is what makes "returned to the level" and "held above it" robust —
a level is a band, not a price, and this is the minimum version of that idea without
building full S/R zones (explicitly out of scope).

**`scoring_model.py` splits the verdict, additively.** A new `EntryScore` is computed
alongside the existing `QualityScore` when `entry.quality` is on:

```python
@dataclass(frozen=True)
class EntryScore:
    total: float                    # 0-100
    verdict: EntryVerdict           # ACTIONABLE | WAIT
    blockers: tuple[str, ...]       # why it is WAIT, e.g. "extended 4.1 ATR above EMA20"
    trigger: str | None             # what would change it
    components: tuple[CategoryScore, ...]
```

Entry components: extension (from `ema20_distance_atr` against `max_extension_atr`),
short-term momentum turn (Stochastic RSI %K crossing %D toward the trade), retest state, and
reward-to-risk from the proposed zone. `QualityScore` keeps its current meaning and weights
untouched — it becomes *trend* quality explicitly rather than by reinterpretation.

**The RSI-extension defect is fixed inside the momentum category, behind the same flag.**
Today `rsi_c = rsi / 100.0`, so RSI 90 earns 0.9 toward a long. With `entry.quality` on, the
momentum category uses a plateau — full credit through a healthy band, tapering above it —
so trend strength stops doubling as entry quality. With the flag off the current formula
runs unchanged, which is what makes the delta attributable.

**`trade_planner.py` gains an optional zone.** `TradePlan` grows
`entry_low`/`entry_high`/`entry_trigger`/`actionable`, populated only when `entry.zones` is
on; `entry` remains for compatibility and equals the zone midpoint when a zone exists.
`risk_reward` is computed from the zone edge that would actually be filled, not the latest
close. A zone that does not contain the current price sets `actionable=False`.

**Measurement is a CLI capability, not a spreadsheet.** `backtest` gains
`--baseline FILE`: after rendering its own report it loads a previously-written `--json`
report and prints a delta table (expectancy, profit factor, win rate, max drawdown, trade
count). Same command, same window, one flag flipped, two JSON files, one honest table. The
recorded baseline for this feature is `2022-01-01 → 2026-07-31` on the existing warm cache
(352 files, 88 symbols × 4 timeframes): **1,362 trades, 35.8% win rate, +0.065R expectancy,
PF 1.10, 75.1% max drawdown.**

**Brief and CLI wiring.** `brief.py`'s `TimeframeBrief` grows the Stochastic RSI pair and
the extension figure, and `SetupBrief` grows the retest state and the entry verdict, so the
AI analyst reasons from the same facts. `_render_setups` groups actionable setups and
waiting setups into separate tables. The momentum path is untouched unless a flag is
explicitly extended to it.

### Automated Testing Decisions

A good test here asserts a measured outcome or an exact classification, never an
implementation detail. The existing suite is the prior art: hand-built frames, injected
fakes at every I/O boundary, no network, no wall-clock sleeps, and
`tests/test_core_independence.py` policing the core's import graph.

- **`indicators.py`** — unit. Stochastic RSI %K/%D match a hand-computed expectation on a known series; short history yields `NaN`; `ema20_distance_atr` is positive above the EMA and negative below, and scales with ATR. Existing feature values are asserted unchanged on a fixed frame, which is what proves the addition is additive.
- **`retest.py`** — unit, the densest module here. Four hand-built sequences, one per `RetestState`, for a long and mirrored for a short. Plus: a break within tolerance is not a break; a return within tolerance counts as a retest; a level lost by a wick but not a close does not fail; a window shorter than `lookback` degrades to `NONE` rather than raising.
- **`scoring_model.py`** — unit. With `entry.quality` off, scores on a fixed fixture are identical to the current values (the regression guard). With it on: an extended coin scores lower on entry than a pulled-back one with otherwise identical inputs; RSI 90 does not out-score RSI 60 on entry; a WAIT verdict carries at least one blocker and a trigger; every blocker string names a measurable quantity.
- **`trade_planner.py`** — unit. A zone contains the level it was derived from; reward-to-risk is computed from the zone edge, not the latest close; a zone far from price yields `actionable=False` and one containing price yields `True`; with `entry.zones` off the plan is identical to today's.
- **`config.py`** — unit, extending the existing pattern. The block parses with defaults when absent; every flag defaults false; unknown keys inside `entry:` are rejected by name; out-of-range tunables are rejected.
- **`backtest --baseline`** — integration against two hand-written JSON reports in `tmp_path`: the delta table reports the correct signed differences, a missing baseline file fails cleanly, and a baseline from a different window is flagged rather than silently compared.
- **Baseline reproduction** — one integration test runs the backtester over the deterministic demo history with all flags off and asserts the report is byte-identical to a stored fixture. The full 2022-2026 reproduction is a documented manual step, not a unit test, because it needs the 575MB cache.
- **`cli.py`** — integration with injected fakes: actionable and waiting setups render as separate groups; the brief carries the new fields; with all flags off the scan output matches the pre-existing tests unmodified.
- **`tests/test_core_independence.py`** — extended to cover `retest` and any other new module by name.

No test may perform network I/O, read a real credential, or write outside `tmp_path`.
