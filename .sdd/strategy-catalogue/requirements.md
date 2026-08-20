# Strategy Catalogue — Trading With the Trend (1A / 1B)

## Problem Statement

The engine answers one question — *"is this coin bullish or bearish?"* — and reports the
answer as if it were the only question that mattered. The trader's actual method asks two,
and the second one is where the money is:

1. **What is the trend?**
2. **Is there a valid entry right now?**

A trend existing is not an entry. The trader's coaches teach a specific, named setup —
*trend pullback* — with an explicit checklist, and the engine implements none of it as a
setup. It has no concept of a named strategy at all. It produces a 0-100 number, and a high
number is read as "trade this", which is exactly the misread the method is designed to
prevent.

The concrete failure, in the trader's own words:

```
EMA10 > EMA21 > EMA50 > SMA200   OK
Higher highs + higher lows       OK
MACD bullish                     OK
Price just exploded far above EMA21   <-- extended
Stoch RSI 94                          <-- exhausted
```

A simplistic trend algorithm returns **LONG**. The method returns **WAIT** — the entry was
missed, and the right response is to watch for the pullback, not to chase. Today's engine
returns the former. It scored a coin in exactly this state highest of the whole watchlist on
a live run, while the model reviewing the same coin independently said *"momentum extremely
stretched, chasing here has poor timing, prefer a pullback."*

The indicators the method is built on are largely absent. The engine computes an EMA 20/50/200
stack; the method uses **EMA10, EMA21, EMA50 and SMA200** — different periods, and a simple
moving average the engine does not compute at any length. There is no trendline of any kind,
only isolated swing pivots. There is no candlestick pattern recognition, so "a bullish
engulfing at the retracement zone" is inexpressible. Stochastic RSI is computed but only ever
read as a position (%K above %D), never as the *cross up from oversold* the method requires.
MACD is checked for position but never for a crossover event or its side of the zero line.

The AI analyst cannot cover the gap. Its prompt names no strategies, so it invents a framework
on each run, and the evidence it receives carries no MACD value, no individual moving-average
value, no candles and no volume series — so it could not check the method's conditions even if
it were asked to.

## Solution

Give the engine a **catalogue of named strategies** it can recognise, starting with the two
the trader has specified in full: **1A — Long with the trend (pullback)** and **1B — Short
with the trend (retracement)**. Each is a checklist of conditions, each condition is evaluated
from the coin's own candles, and the result is reported as four separate facts rather than one
number:

```
Trend:        BEARISH
Setup:        trend pullback
Entry status: NOT READY
Decision:     WAIT
Reason:       Price is extended below the retracement zone. Wait for a rally toward
              EMA21-EMA50 and bearish confirmation.
```

**WAIT is a first-class answer, not a failure.** A scan of a healthy watchlist should return
WAIT for most coins most of the time, because most coins most of the time are in a trend
without being at an entry. Nothing in the design pushes the scanner toward finding a trade.

The missing indicators are added because the method needs them, not because more indicators
are better: **EMA10, EMA21 and SMA200** alongside the existing stack; a fitted **trendline**
through swing pivots so "price returned to the rising trendline" can be tested; **candlestick
reaction patterns** — engulfing, hammer and shooting star, morning and evening star — so the
confirmation candle at the zone is visible; **Stochastic RSI read as a cross** from oversold or
overbought rather than as a level; and **MACD read as a crossover event and a zero-line side**
rather than only as a position.

Every condition the detector evaluates is also handed to the AI analyst as a stated fact, and
the strategies are **named in its instructions**, so the model reasons about the same setups
against the same evidence instead of improvising. Where the detector says the checklist is
unmet, the model sees precisely which condition failed and what would clear it.

The indicator set is closed to exactly what the method uses: **EMA10, EMA21, EMA50, SMA200,
Stochastic RSI, MACD, ATR, volume and the raw candles.** Everything the engine computed
before that is not on that list is removed — EMA20 and EMA200, standalone RSI, rate of change,
on-balance volume and Bollinger band width. Nothing is kept because it is already there.

That removal retires the **0-100 quality score**, and it has to: three of its eight categories
were built from the removed indicators — trend from the EMA 20/50/200 ordering, momentum from
RSI and rate of change, volume from the OBV slope. A score missing three categories is not the
same score, so rather than quietly degrade it, it goes, along with the quality threshold that
gated on it. The direction rule moves onto the method's own stack. The catalogue becomes the
engine rather than an addition to it.

## User Stories

1. As a trader, I want the engine to recognise a named trend-pullback setup, so that it reports the setup I was actually taught rather than a generic score.
2. As a trader, I want the trend and the entry judged separately, so that a good trend at a bad price is never presented as a trade.
3. As a trader, I want WAIT to be a normal answer, so that the scanner is not pushed into finding a trade on a day when there isn't one.
4. As a trader, I want to be told which condition of the checklist is unmet, so that I know exactly what I am waiting for.
5. As a trader, I want the reason to name the measured value, so that I can check the engine's claim against my own chart.
6. As a trader, I want EMA10, EMA21 and EMA50 computed per timeframe, so that the moving-average stack matches the one my method uses.
7. As a trader, I want a 200-period simple moving average computed per timeframe, so that the long-term filter is the one my method uses rather than an exponential substitute.
8. As a trader, I want the full stack ordering tested as a single condition, so that "EMA10 > EMA21 > EMA50 > SMA200" is either satisfied or not.
9. As a trader, I want higher highs and higher lows confirmed for a long, so that an uptrend is established by structure and not by moving averages alone.
10. As a trader, I want lower highs and lower lows confirmed for a short, so that the mirror condition is equally strict.
11. As a trader, I want a rising trendline fitted through recent swing lows, so that "price returned to the trendline" is a measurable event.
12. As a trader, I want a falling trendline fitted through recent swing highs, so that a short retracement into resistance is equally measurable.
13. As a trader, I want the retracement zone defined as the band between EMA21 and EMA50, so that the pullback area matches the one I am taught to buy into.
14. As a trader, I want to know whether price is above, inside or below that zone, so that "extended", "at the entry" and "broken down" are distinguished.
15. As a trader, I want a bullish engulfing candle detected, so that a reaction at the zone is confirmed by price action.
16. As a trader, I want a hammer or pin-bar rejection detected, so that a wick-based reaction counts as confirmation.
17. As a trader, I want a morning-star reversal detected, so that a three-candle reversal counts as confirmation.
18. As a trader, I want the bearish mirrors — bearish engulfing, shooting star, evening star — detected for shorts, so that both sides of the method are served equally.
19. As a trader, I want Stochastic RSI to count only when %K crosses %D in the trade's direction, so that merely being oversold is not mistaken for a signal.
20. As a trader, I want the cross to count only when it happens at or near an extreme, so that a mid-range wobble does not qualify as a pullback signal.
21. As a trader, I want MACD's crossover recognised as an event, so that "just turned bullish" is distinguishable from "has been bullish for weeks".
22. As a trader, I want MACD's position relative to zero reported, so that I can tell a crossover above zero from one below it.
23. As a trader, I want a setup that is extended far beyond the retracement zone to be reported as WAIT, so that I never chase a move I have missed.
24. As a trader, I want a setup that has dumped far below the zone to be reported as WAIT for the short side too, so that I do not chase a short into support.
25. As a trader, I want each strategy evaluated on a decision timeframe with higher timeframes as context, so that the read matches how I look at a chart.
26. As a trader, I want the scan to list which strategy matched each coin, so that I can see at a glance whether it is a pullback, a breakout or nothing.
27. As a trader, I want coins where no strategy matches to be reported as such, so that silence is explicit rather than an empty table.
28. As a trader, I want the AI analyst told the names and rules of the strategies, so that it evaluates my method rather than inventing its own.
29. As a trader, I want the analyst to receive every condition's measured value, so that its opinion rests on the same facts as the detector's verdict.
30. As a trader, I want the analyst to be able to disagree with the detector, so that a checklist that is technically met but contextually wrong can still be flagged.
31. As a trader, I want the analyst to state which strategy it judged, so that its verdict is comparable with the detector's.
32. As a trader, I want the raw candles available to the analyst for the decision timeframe, so that it can see price action the detector's patterns do not cover.
33. As a trader, I want only the indicators my method uses to be computed, so that the engine reflects what I was taught rather than accumulating everything anyone once tried.
34. As a trader, I want indicators I did not ask for removed rather than left unused, so that the evidence I read and the evidence the model reads contain no distractions.
35. As a trader, I want the 0-100 quality score retired along with the indicators behind it, so that the engine gives one answer in my own terms instead of two answers in different languages.
36. As a trader, I want the direction rule to use my moving-average stack, so that what the engine calls an uptrend is what my method calls an uptrend.
37. As a trader, I want the momentum scanner to keep working, so that removing indicators from the trend engine does not cost me the other scanner.
38. As a trader, I want every threshold in the checklist to be configurable, so that I can match my coaches' exact parameters as I learn them.
39. As a trader, I want sensible defaults for every threshold, so that the strategies work before I have tuned anything.
40. As a trader, I want the detectors to run without an AI key, so that the engine's own reading is available for free.
41. As a trader, I want each actionable coin to carry a confidence figure, so that I can tell a marginal call from a strong one.
42. As a trader, I want the actionable list ordered best-first, so that I read the strongest candidate before the weakest.
43. As a trader, I want a defensible order even when the analyst has not run, so that the free reading is still usable as a ranked list.
44. As a trader, I want every plan to pay at least 1:2, so that a setup that cannot return twice my risk is never presented to me.
45. As a trader, I want to be told when a plan's target was manufactured to reach that ratio rather than found in the chart, so that I know whether the number on screen is a level the market respects or arithmetic.
46. As a trader, I want a setup aiming at a real level ranked above one aiming at a manufactured target, so that the better trade is read first.
47. As a trader, I want a manufactured target never to block an entry, so that a coin making new highs — which by definition has no level above it — is still tradeable.
48. As a trader, I want the preferred ratio configurable, so that I can relax or tighten it as my method develops.
49. As a maintainer, I want each new indicator computed in the existing pure indicator layer, so that it is testable without network access and reusable by both scanners.
50. As a maintainer, I want the trendline fitter to be a pure function over pivots, so that its behaviour on a known series can be asserted exactly.
51. As a maintainer, I want each candlestick pattern to be a pure predicate over candles, so that a hand-built three-bar sequence asserts an exact classification.
52. As a maintainer, I want the strategy detector to be a pure function over computed facts, so that a full checklist can be exercised without candles at all.
53. As a maintainer, I want the new modules free of terminal-rendering dependencies, consistent with the rest of the codebase.
54. As a maintainer, I want the strategy verdict to carry a per-condition breakdown, so that a wrong verdict can be traced to the condition that produced it.

## User Acceptance Tests

1. Given a coin whose EMA10, EMA21, EMA50 and SMA200 are stacked in ascending order on the decision timeframe, when it is analysed, then the stack condition for a long is reported as satisfied.
2. Given a coin whose moving averages are stacked in any other order, when it is analysed, then the stack condition is reported as unsatisfied and named as the failing condition.
3. Given a coin making higher highs and higher lows, when it is analysed, then the structure condition for a long is satisfied.
4. Given a coin making lower highs and lower lows, when it is analysed, then the structure condition for a short is satisfied.
5. Given a series of ascending swing lows, when a trendline is fitted, then it is reported as rising with the level it currently sits at.
6. Given a series of descending swing highs, when a trendline is fitted, then it is reported as falling with its current level.
7. Given too few swing pivots to define a line, when a trendline is fitted, then it is reported as absent rather than as a fabricated line.
8. Given a coin trading above both EMA21 and EMA50, when its position is reported, then it is described as above the retracement zone.
9. Given a coin trading between EMA21 and EMA50, when its position is reported, then it is described as inside the retracement zone.
10. Given a coin trading below both, when its position is reported, then it is described as below the zone.
11. Given a candle that closes higher and whose body fully covers the previous falling candle's body, when it is analysed, then a bullish engulfing is reported.
12. Given a candle with a long lower wick, a small body near the top of its range and a short upper wick, when it is analysed, then a hammer is reported.
13. Given the mirrored candle with a long upper wick, when it is analysed, then a shooting star is reported.
14. Given a three-candle down-small-up sequence closing above the first candle's midpoint, when it is analysed, then a morning star is reported.
15. Given the mirrored three-candle sequence, when it is analysed, then an evening star is reported.
16. Given an ordinary candle matching none of the patterns, when it is analysed, then no pattern is reported rather than the nearest match.
17. Given a Stochastic RSI whose %K has just crossed above %D from below the oversold level, when it is analysed, then a bullish momentum turn is reported.
18. Given a Stochastic RSI that is deeply oversold but whose %K has not crossed %D, when it is analysed, then no momentum turn is reported.
19. Given a Stochastic RSI whose %K crosses %D in the middle of its range, when it is analysed, then no momentum turn is reported.
20. Given a MACD line that has just crossed above its signal line, when it is analysed, then a bullish crossover is reported together with which side of zero it occurred on.
21. Given a MACD line that has been above its signal line for many bars, when it is analysed, then it is reported as bullish in position but not as a fresh crossover.
22. Given a coin satisfying every 1A condition, when it is analysed, then the decision is LONG, the setup is named as the trend pullback, and the entry status is ready.
23. Given a coin in a clean uptrend whose price is far above the retracement zone with Stochastic RSI at an extreme, when it is analysed, then the decision is WAIT and the reason states that price is extended and names the zone to wait for.
24. Given a coin in a clean downtrend whose price has fallen far below the zone, when it is analysed, then the decision is WAIT and the reason says not to chase the short.
25. Given a coin in an uptrend, inside the zone, with a bullish reaction candle, but with no Stochastic RSI cross, when it is analysed, then the decision is WAIT and the unmet condition named is the momentum turn.
26. Given a coin whose trend conditions fail outright, when it is analysed, then no setup is named and the decision is that no strategy matched.
27. Given any WAIT verdict, when the reason is read, then it names at least one measured quantity together with its value.
28. Given a scan, when the results are displayed, then each coin shows its trend, the setup matched, the entry status and the decision as separate fields.
29. Given a scan in which no coin is at an entry, when the results are displayed, then the output says so plainly rather than presenting the best of a bad list.
30. Given the AI analyst is enabled, when its instructions are inspected, then strategies 1A and 1B are named and their conditions stated.
31. Given the AI analyst is enabled, when its evidence is inspected, then every moving average value, the trendline, the zone position, any detected candlestick pattern, the Stochastic RSI cross state and the MACD crossover state are present.
32. Given the AI analyst is enabled, when a verdict is returned, then it states which strategy it judged and its own entry-status reading.
33. Given the AI analyst disagrees with the detector, when the results are displayed, then both readings are shown side by side rather than one silently overriding the other.
34. Given the computed indicator set is inspected, when it is listed, then it contains exactly the method's indicators and none of the removed ones.
35. Given a configuration file that names a removed indicator or the retired quality threshold, when it is loaded, then it is rejected by name rather than silently ignored.
36. Given a scan is run, when the output is read, then no 0-100 quality score appears anywhere in it.
37. Given a coin whose moving averages are stacked in ascending order on the higher timeframes, when its direction is decided, then the decision uses the method's stack and not the retired one.
38. Given the momentum scanner, when it is run after the removal, then it still produces its ranked movers with their acceleration factor.
39. Given a threshold is changed in configuration, when a coin is re-analysed, then the affected condition's outcome reflects the new threshold.
40. Given no AI credential is configured, when a scan is run, then the detector's own verdicts are still produced and displayed.
41. Given a timeframe with too little history for the 200-period average, when it is analysed, then the stack condition is reported as unknown rather than as failed.
42. Given a scan with the analyst enabled, when the results are displayed, then each actionable coin shows a long or short decision together with a 0-100 confidence.
43. Given several coins are actionable, when the results are displayed, then they are ordered by confidence, highest first.
44. Given the analyst is not enabled, when the results are displayed, then coins are ordered by how many of the checklist's conditions they meet.
45. Given a coin whose checklist is further from complete than the configured floor, when the analyst runs, then that coin is not sent for review.
46. Given any plan the engine produces, when its reward-to-risk is read, then it is at or above the configured preference.
47. Given a plan whose take-profit is a real swing level, when the plan is displayed, then it is marked as a structural target.
48. Given a plan whose take-profit had to be pushed out to reach the ratio, when the plan is displayed, then it is marked as a manufactured target.
49. Given a coin at new highs with no swing level above it, when it is analysed, then its manufactured target does not prevent it from reaching entry ready.
50. Given two setups meeting the same conditions, when they are ordered, then the one aiming at a structural target is listed first.

## Definition of Done

- All user acceptance tests pass.
- Strategies 1A and 1B are each implemented as a named, individually inspectable checklist.
- Every condition in each checklist reports its own outcome and the measured value behind it.
- The four-part output — trend, setup, entry status, decision — is produced for every analysed coin.
- WAIT is reachable from every distinct unmet condition, and each WAIT names what would clear it.
- The computed indicator set is exactly the method's list; no removed indicator is computed, stored, displayed or sent to the model anywhere in the codebase.
- The 0-100 quality score, its categories, its weights and its threshold are removed rather than left unreachable, and no dead code referring to them remains.
- The direction rule decides from the method's moving-average stack.
- The momentum scanner still runs and still produces its acceleration factor.
- A configuration naming a removed setting is rejected by name with a message saying what replaced it.
- Every actionable coin carries a long or short decision and a 0-100 confidence, and the actionable list is ordered best-first with or without the analyst.
- Every plan pays at least the configured reward-to-risk preference, a manufactured target is labelled as such wherever a plan is shown, and no manufactured target blocks an entry.
- The AI analyst's instructions name both strategies and state their conditions.
- The AI analyst's evidence carries every value its instructions ask it to reason about.
- Every threshold is configurable and every one has a documented default.
- The new analysis modules import no terminal-rendering libraries.
- The full pre-existing test suite passes, and the project's quality gates — tests, lint, type check, compile — are green.
- User-facing documentation describes each strategy, its conditions and its configurable thresholds.

## Out of Scope

- **Order execution.** Unchanged: this remains advisory.
- **Strategies 2 and 3.** Breakout and breakout-plus-retest are named in the catalogue but their conditions have not yet been specified; they are separate features. The existing `retest` detector and the momentum scanner are their nearest current relatives and are untouched here.
- **Backtesting the strategies.** Measurement is parked by explicit decision. No claim about profitability, win rate or edge may be made anywhere in this feature's output or documentation.
- **Re-adding any removed indicator.** The indicator set is closed. Additions happen when the trader asks, as a separate change.
- **Rebuilding a numeric score from the method's indicators.** The catalogue's output is a checklist and a decision, not a number. A score may be desirable later; it is not this feature.
- **Support/resistance zones from clustered pivots.** The trendline fitter works from existing swing pivots.
- **Automatic threshold tuning.** Thresholds are configured by hand from the trader's own material.

## Further Notes

The ordering principle for this feature is that the trader's method is the specification. Where
the method and the current engine disagree — EMA periods, simple versus exponential, level
versus cross — the method wins, and the engine's existing behaviour is preserved alongside
rather than argued with.

Two of the method's conditions are materially harder than the rest and should be expected to
need iteration: the fitted trendline, because a line through pivots is sensitive to which
pivots are chosen, and the candlestick patterns, because textbook definitions vary and the
trader's coaches may use tighter or looser ones. Both are built so their parameters are
configurable and their behaviour is asserted on hand-built series, which is what makes a later
correction cheap.

The feature deliberately produces its verdict twice — once from the deterministic detector and
once from the model reading the same facts. Neither overrides the other. Showing both is what
makes it possible, later, to tell which one is worth listening to.

---

## Technical Annex
> Written against codebase as of: 2026-08-19

### Architectural Decisions

**Replacement, not addition.** The indicator set is closed to the method's list, and the
0-100 `QualityScore` is deleted along with `IMPLEMENTED_CATEGORIES`, the category weights,
`surface()` and `quality_threshold`. This is forced rather than chosen: trend, momentum and
volume were computed from the removed indicators, so three of eight categories have no inputs
left. Deleting is preferred to degrading, because a score quietly missing three categories
would keep the same name and the same 0-100 range while meaning something different.

Deleted with it: the `entry:` configuration block and everything behind it — `rsi_plateau`
(operates on the removed RSI), `extension_guard` and `zones` (on the removed EMA20), and the
`EntryScore` built on those. Their purpose, refusing an extended entry, is served by the
catalogue's zone condition using the method's own numbers. `retest.py` is **kept** untouched:
it works from swing levels and ATR, neither of which is removed, and it is Strategy 3's
starting point.

**`indicators.py` — the closed set.** `TimeframeFeatures` becomes exactly:

```python
symbol, timeframe, limited_history
ema10, ema21, ema50, sma200          # the method's stack
macd, macd_signal, macd_hist         # + crossover event and zero-line side
stoch_rsi_k, stoch_rsi_d             # + cross event and the extreme it came from
atr
volume, relative_volume
```

`ema20`, `ema200`, `rsi`, `roc`, `obv`, `obv_slope`, `bollinger_width` and
`ema20_distance_atr` are removed. `SMAIndicator` from `ta.trend` supplies the simple average
(verified present in the installed `ta`). The Stochastic RSI is still computed by
`StochRSIIndicator`, which derives it from RSI internally — that is the library's business and
is not a standalone RSI on the bundle.

**`direction.py` moves onto the method's stack.** Its `ema20 > ema50 > ema200` test becomes
`ema10 > ema21 > ema50 > sma200`, mirrored for a downtrend. Every direction decision the
engine has ever made will differ, which is expected and is the point.

**`momentum.py` keeps working.** It reads `features.roc` once, for its acceleration factor. It
already has `period_return(candles, lookback)`, so it computes that value locally instead.

Also added, because the method reads them as events rather than levels:
`macd_crossed_up` / `macd_crossed_down` (the line crossed its signal within the last
`cross_lookback` bars), `stoch_crossed_up` / `stoch_crossed_down`, and the extreme each cross
came from. These are derived inside `compute_features` from the series it already builds.

**New pure module `trendline.py`.** A least-squares fit through the most recent swing pivots:

```python
@dataclass(frozen=True)
class Trendline:
    slope: float           # price per bar; sign gives rising/falling
    level_now: float       # the fitted line's value at the latest bar
    touches: int           # pivots the line was fitted through
    r_squared: float       # how well the pivots actually lie on a line

def fit(pivots: Sequence[tuple[int, float]], *, min_touches: int = 3) -> Trendline | None
def distance_atr(line: Trendline, close: float, atr: float) -> float
```

`None` when there are fewer than `min_touches` pivots or the fit is degenerate — an absent
line, never a fabricated one. `r_squared` exists so a line drawn through pivots that are not
actually collinear can be rejected by threshold rather than silently trusted.

**New pure module `patterns.py`.** One predicate per pattern over the tail of a `Candles`
frame, plus a detector that returns every pattern present at the latest bar:

```python
class Pattern(StrEnum):
    BULLISH_ENGULFING = "bullish_engulfing"
    BEARISH_ENGULFING = "bearish_engulfing"
    HAMMER = "hammer"
    SHOOTING_STAR = "shooting_star"
    MORNING_STAR = "morning_star"
    EVENING_STAR = "evening_star"

def detect(candles: Candles, *, wick_body_ratio: float = 2.0) -> tuple[Pattern, ...]
def is_bullish(pattern: Pattern) -> bool
```

Definitions are pinned in code and asserted on hand-built bars: engulfing requires the current
body to cover the prior body and close the opposite way; hammer requires a lower wick of at
least `wick_body_ratio` times the body with an upper wick no larger than the body; the star
patterns require the three-bar sequence with the third closing beyond the first's midpoint.

**New pure module `strategy.py` — the deep module of this feature.** It reads *computed facts*,
never candles, which is what lets a full checklist be exercised in a unit test without building
a price series:

```python
class StrategyId(StrEnum):
    TREND_PULLBACK_LONG = "1A"
    TREND_PULLBACK_SHORT = "1B"

class EntryStatus(StrEnum):
    READY = "READY"
    NOT_READY = "NOT_READY"

@dataclass(frozen=True)
class Condition:
    name: str            # "ma_stack", "structure", "zone", "reaction", "stoch_turn", "macd"
    met: bool | None     # None = not measurable (insufficient history)
    detail: str          # "EMA10 0.0031 > EMA21 0.0029 > EMA50 0.0027 > SMA200 0.0024"

@dataclass(frozen=True)
class SetupVerdict:
    symbol: str
    trend: Direction              # what the trend is
    strategy: StrategyId | None   # which setup was matched, None when none applies
    entry_status: EntryStatus
    decision: Direction           # LONG / SHORT / NONE, where NONE renders as WAIT
    reason: str
    conditions: tuple[Condition, ...]

def evaluate(...) -> SetupVerdict
```

The decision rule is deliberately mechanical: the *trend* conditions (stack, structure,
trendline) decide `trend`; the *entry* conditions (zone position, reaction candle, Stoch cross,
MACD) decide `entry_status`; `decision` is the trend direction only when every entry condition
is met, and WAIT otherwise. `reason` is composed from the unmet conditions' `detail` strings,
so it always names measured values and cannot drift from the checklist.

**Reward-to-risk is a preference, not a condition — decided 2026-08-20.**
`TradePlanner._long_take_profit` floors the target at `entry + target_rr * risk` when no swing
high is far enough away, so `TradePlan.risk_reward` is at least `target_rr` by construction:
1:2 is already guaranteed and a condition testing it would be vacuous.

Testing the *structural* target instead was considered and rejected. A coin at new highs has no
overhead pivot by definition, so the target is always floored and the condition would always
fail — permanently blinding 1A to the very setups Strategy 2 exists to catch. Blocking would
also add no discipline, since the ratio cannot be violated anyway. And measured 2026-08-19 on
the live watchlist, 38 of 42 planned coins were floored against 4 structural, so it would cost
a 90% rejection rate on a hypothesis that measurement is parked and cannot test.

`TradePlan` therefore grows a boolean recording whether its take-profit came from a pivot or
from the floor. It is rendered wherever a plan is shown and used as a ranking tiebreak between
setups meeting the same conditions. It gates nothing. The blocking checklist stays at seven
conditions.

**Zone position** is a small enum derived from EMA21/EMA50 and ATR — `ABOVE`, `INSIDE`,
`BELOW`, plus how many ATR outside the band price sits, which is what makes "extended" a
number rather than an adjective.

**`config.py` gains a `strategies:` block.** Enabled by default, because the feature adds
output without altering any existing value:

```yaml
strategies:
  enabled: true
  decision_timeframe: 4h        # the timeframe the checklist is evaluated on
  context_timeframes: [1d]      # higher timeframes that must agree on trend
  stoch_oversold: 0.20          # %K/%D level a bullish cross must come from
  stoch_overbought: 0.80
  cross_lookback: 3             # bars within which a cross counts as "just happened"
  zone_tolerance_atr: 0.25      # how far outside EMA21-EMA50 still counts as at the zone
  max_extension_atr: 2.0        # beyond this, the entry is missed -> WAIT
  # Reward-to-risk is read from the existing top-level `target_rr` (default 2.0, i.e. 1:2)
  # rather than duplicated here, so the plan and the condition can never disagree.
  trendline_min_touches: 3
  trendline_min_r2: 0.7
  pattern_wick_body_ratio: 2.0
```

Unknown keys rejected by name and every numeric range validated, following the existing
`entry:` block's pattern exactly.

**`brief.py` and `openai_analyst.py`.** `TimeframeBrief` grows the new moving averages, the
zone position, the trendline, the detected patterns and the two cross states. `SetupBrief`
grows the `SetupVerdict`. `SYSTEM_PROMPT` gains a section naming strategies 1A and 1B and
listing their conditions verbatim, plus the instruction that WAIT is expected to be the common
answer and that the model must state which strategy it judged. The response schema grows
`strategy` and `entry_status` per verdict.

Raw candles for the decision timeframe are included in the evidence at a bounded bar count so
the model can read price action the pattern detector does not cover; the count is configurable
because it is the single largest contributor to prompt size.

**`cli.py`.** A new Setups table — Symbol, Trend, Setup, Entry, Decision, Reason — rendered
beside the existing High-conviction table, never in place of it. When no coin reaches READY the
table says so explicitly rather than rendering empty. Detector and analyst verdicts are shown
side by side when they differ.

**`tests/test_core_independence.py`** is extended to cover `trendline`, `patterns` and
`strategy` by name.

### Automated Testing Decisions

A good test here asserts an exact classification or a measured outcome, never an
implementation detail. The existing suite is the prior art: hand-built frames, injected fakes
at every I/O boundary, no network, no wall-clock sleeps. `tests/test_retest.py` is the closest
model — four hand-built sequences, one per outcome, mirrored for the short side.

- **`indicators.py`** — unit. `ema10`/`ema21`/`sma200` match hand-computed values on a known series; short history yields `NaN`; the cross flags fire on the bar the cross happens and stop firing after `cross_lookback` bars. A fixed-frame regression test asserts every pre-existing field is unchanged, which is what proves the addition is additive.
- **`trendline.py`** — unit. Ascending pivots fit a rising line and descending a falling one; a perfectly collinear set yields `r_squared` of 1.0; scattered pivots fall below the threshold; fewer than `min_touches` yields `None`; `distance_atr` scales with ATR.
- **`patterns.py`** — unit, the densest module. One hand-built bar sequence per `Pattern`, each asserted to produce that pattern and no other, mirrored bull/bear. Plus negatives: an ordinary candle yields nothing, a body-equal candle is not an engulfing, a hammer with an oversized upper wick is not a hammer, and a two-bar frame does not raise when a three-bar pattern is sought.
- **`strategy.py`** — unit, and the most valuable. Because `evaluate` reads facts rather than candles, each checklist row gets a test that flips exactly one condition and asserts the verdict changes as specified: the full-house case yields LONG/READY; the extended case yields WAIT with extension named; the no-cross case yields WAIT with the momentum turn named; a failing trend condition yields no strategy at all. Every WAIT is asserted to carry at least one measured value in its reason. Mirrored for 1B.
- **`config.py`** — unit, extending the existing pattern. The block parses with defaults when absent; unknown keys inside `strategies:` are rejected by name; out-of-range thresholds are rejected; the shipped example config loads.
- **`brief.py` / `openai_analyst.py`** — unit. The brief carries every field the prompt asks about, and stays strictly JSON-encodable with `allow_nan=False`; the prompt names both strategies; the response schema accepts a verdict carrying `strategy` and `entry_status` and rejects one missing them.
- **`cli.py`** — integration with injected fakes: the Setups table renders the four fields; a scan with nothing ready says so; detector and analyst disagreement renders both; with the catalogue off the output matches the pre-existing tests unmodified.
- **Removal** — asserted structurally rather than by hope: a test enumerates the fields of `TimeframeFeatures` and fails if any name outside the closed list appears, and a source-level test greps the package for the removed identifiers so a reintroduction anywhere is caught. A configuration naming a removed key is asserted to raise with a message naming the replacement.
- **The momentum scanner** — its existing tests must pass unmodified after it stops reading the removed field, which is what proves the removal did not silently change its scores.

No test may perform network I/O, read a real credential, or write outside a temporary
directory.
