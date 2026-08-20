# Strategy catalogue — collected rules

The bot is moving from **one monolithic score** to a **catalogue of named strategies** the
analyst evaluates independently. This file collects the rules as they are specified. It is a
requirements document, not a design: no decision has been made here about which parts are
computed deterministically and which the model judges.

The governing idea, which applies to every strategy below: **ask two questions, not one.**

1. What is the trend?
2. Is there a valid entry *now*?

so that the output reads

```
Trend:        BEARISH
Setup:        trend pullback
Entry status: NOT READY
Decision:     WAIT
Reason:       Price is extended below the retracement zone. Wait for a rally toward
              EMA21-EMA50 and bearish confirmation.
```

**WAIT is a legitimate, common answer.** The scanner must never be forced to find a trade.
A trend existing is not an entry; most scans of a healthy watchlist should return WAIT.

---

## The indicator set — closed list

Fixed by the trader 2026-08-19: **these and nothing else.** Anything the engine used before
that is not on this list is removed. Additions happen only when the trader asks for them.

| Indicator | Used for |
| --- | --- |
| EMA10, EMA21, EMA50 | The fast half of the trend stack |
| SMA200 | The long-term filter — a *simple* average, not exponential |
| Stochastic RSI (%K, %D) | The momentum turn, read as a cross from an extreme |
| MACD (line, signal, histogram) | Confirmation, read as a crossover event and a zero-line side |
| ATR | Distances in volatility terms — zone tolerance, extension, stops |
| Volume (raw and relative) | Context, and part of the evidence sent to the analyst |
| Raw candles | Structure, trendlines, candlestick patterns, and the analyst's own reading |

**Removed:** EMA20 and EMA200 (superseded by EMA21 and SMA200), standalone RSI, rate of
change, on-balance volume and its slope, Bollinger band width.

### What removal costs, recorded so it is not rediscovered later

The removed indicators were not decorative — they were the inputs to three of the old scoring
model's eight categories, so removing them retires the model:

- **Trend** was the EMA 20/50/200 stack ordering.
- **Momentum** was the mean of RSI, MACD and rate of change.
- **Volume** was relative volume paired with the OBV slope.

Therefore the **0-100 quality score and its `quality_threshold` are retired**, and the
direction rule — which decided LONG/SHORT from the EMA 20/50/200 stack — moves onto the
method's stack. The strategy catalogue becomes the engine rather than an addition to it.

Also retired: the `entry.*` flags, which operated on RSI (`rsi_plateau`) and EMA20
(`extension_guard`, `zones`, and the entry score built on them). Their job — refusing an
extended entry — is done by the catalogue's zone condition, from the method's own numbers.
`retest.py` survives untouched; it works from swing levels and ATR and is Strategy 3's
starting point.

The `movers` momentum scanner survives. It reads rate of change once, for its acceleration
factor, and already computes period returns itself.

---

## Strategy 1A — Long with the trend (pullback)

**Established uptrend**
- `EMA10 > EMA21 > EMA50 > SMA200`
- Higher highs and higher lows
- Rising trendline

**Pullback into a good area**
- Price returns to support / the rising trendline
- Ideally enters the **EMA21-EMA50 retracement zone**

**Price-action confirmation**
- A bullish reaction candle at that area: bullish engulfing, hammer / pin-bar rejection,
  morning-star-style reversal, or similar

**Stoch RSI confirmation**
- Reaches or approaches oversold
- %K and %D **turn / cross upward** — being oversold alone is not the signal

**MACD confirmation**
- Bullish crossover, and/or bullish position relative to the zero line

**Reward-to-risk — a preference, not a gate**
- Preferred **R:R at least 1:2**, and already guaranteed: every plan's take-profit is floored
  at the ratio, so a sub-1:2 trade cannot be shown
- Whether that target is a real swing high or one the planner manufactured is **reported**,
  and a structural target ranks above a manufactured one — it never blocks the entry
  (see the note below)

### The case this strategy exists to reject

```
EMA10 > EMA21 > EMA50 > SMA200   OK
HH + HL                          OK
MACD bullish                     OK
Price just exploded far above EMA21   <-- extended
Stoch RSI 94                          <-- exhausted
```

A simplistic trend algorithm returns **LONG**. Strategy 1A returns **WAIT**: the entry was
missed. What it waits for:

```
                 HH
                /\
               /  \
              /    \
             /      \
EMA21 ────────────────●
                      ↑
                   Pullback
                      +
               bullish reaction
                      +
             Stoch RSI turns up
                      +
               MACD confirms
                      ↓
                    LONG
```

---

## Strategy 1B — Short with the trend (retracement)

The mirror of 1A. Note the correction: for a short, price **rejects from resistance / the
downtrend line** — not from support.

**Established downtrend**
- `EMA10 < EMA21 < EMA50 < SMA200`
- Lower highs and lower lows
- Falling trendline

**Retracement**
- Price rallies back toward resistance / the downtrend line
- Ideally enters the **EMA21-EMA50 zone**

**Price-action confirmation**
- Bearish engulfing, shooting star / rejection candle, evening-star-style reversal

**Stoch RSI**
- Reaches or approaches overbought
- %K crosses below %D / momentum turns down

**MACD**
- Bearish crossover, and/or below zero

**Reward-to-risk — a preference, not a gate**
- The mirror: 1:2 guaranteed by the plan's floor, and the next structural support reported as
  real or manufactured

### The case this strategy exists to reject

```
EMA10 < EMA21 < EMA50 < SMA200   OK
LH + LL                          OK
MACD bearish                     OK
Price already dumped far below EMA21
Stoch RSI 5
Price sitting near support
```

**WAIT — do not chase the short.** Wait for a rally into EMA21-EMA50, a bearish rejection,
Stoch RSI turning down and MACD confirming.

---

## Note on reward-to-risk — why it is a preference and not a condition

Decided 2026-08-20, after the alternative was costed.

The trade planner *floors* every take-profit at `target_rr` (default 2.0): when no swing pivot
lies far enough away it pushes the target out to exactly the ratio. So **1:2 is already
guaranteed** — a sub-1:2 plan cannot reach the trader — and a checklist condition testing the
plan's own figure would pass every time and mean nothing.

The tempting fix was to test the *structural* target instead: does the next pivot reach 2R
unaided? That was rejected for three reasons.

**It would permanently blind 1A to breakouts.** A coin at new highs has no swing high above it
by definition, so the planner floors the target, so the condition fails — every time, for
structural reasons rather than because the setup is poor. It would reject exactly the trades
Strategy 2 exists to catch.

**Blocking adds no discipline.** The 1:2 minimum is enforced by the planner already. A gate
would not stop a bad ratio reaching the trader; it would only filter on whether the target
happens to coincide with a pivot, which is a different question wearing the same name.

**It is a 90% rejection on an unmeasured hunch.** Measured on the live 86-coin watchlist
2026-08-19: of 42 coins with a plan, **38 reported exactly 2.00** — the floored value — and
only **4** had a structural target beyond 2R (3.82, 5.52, 7.95, 54.36). With measurement
parked, there would be no way to learn whether that severity helped or merely silenced the
scanner.

**So:** the ratio stays enforced by the plan, the structural-versus-manufactured distinction is
**reported** wherever a plan is shown, and a structural target **ranks above** a manufactured
one between two otherwise equal setups. The blocking checklist stays at seven conditions.

---

## The strategy library

Specified by the trader 2026-08-20. Two families, six setups. The families are kept
**separate** rather than merged, because the entry logic differs: a pullback buys weakness
into a trend, a breakout buys strength through a level.

| # | Strategy | Typical entry |
| --- | --- | --- |
| 1A | Trend Pullback LONG | Pullback + bullish confirmation |
| 1B | Trend Pullback SHORT | Retracement + bearish confirmation |
| 2A | Bullish Breakout | Confirmed resistance break |
| 2B | Bearish Breakout | Confirmed support break |
| 3A | Bullish Breakout + Retest | Former resistance holds as support |
| 3B | Bearish Breakout + Retest | Former support holds as resistance |

### What the two families weigh differently

| | Trend Pullback | Breakout |
| --- | --- | --- |
| Trend required? | **Yes** | **Not necessarily** |
| EMA10/21/50 + SMA200 | Very important | Context |
| HH/HL or LH/LL | Very important | Context |
| Support / resistance | Important | **Critical** |
| Stoch RSI | Entry confirmation | Secondary confirmation |
| MACD | Confirmation | Confirmation |
| Volume | Helpful | **Critical** |
| Candlestick | Critical | Critical |
| Break of level | No | **Critical** |
| Retest | Pullback concept | Very desirable |
| R:R | ≥2 preferred | ≥2 preferred |

The row that matters most for implementation: **a breakout does not require the trend
stack.** The seven-condition checklist behind 1A/1B gates on it, so 2A/2B cannot reuse that
checklist — the stack is context here, not a gate.

---

## Strategy 2 — Breakout

A breakout is **not** `price > resistance → LONG`, nor `price < support → SHORT`. That is a
*break*. What the strategy trades is a **confirmed breakout**, and the distinction between
the two is the whole strategy.

Three inputs carry it: **support/resistance**, **volume**, and **chart structure /
patterns**.

### Strategy 2A — Bullish breakout

Confluence, in order:

- A **resistance level is established** — it has meaningful prior reactions, not one touch
- Price **closes convincingly above** it
- The breakout candle **is not just a wick** through the level
- **Volume expands** against its recent average
- **Structure supports continuation**
- **Room to the next resistance** — somewhere for the trade to go
- **Acceptable R:R**

Then **LONG**.

**If price has already exploded far beyond the breakout level: `WAIT FOR RETEST`** — not
LONG. This is the breakout family's version of the "do not chase" rule that 1A/1B express as
zone extension, and it is the same mistake: a good idea at a bad price.

### Strategy 2B — Bearish breakout

The mirror: support established → **strong close below** support → increased volume →
bearish structure → room before the next support → acceptable R:R → **SHORT**.

Again, if price has already dumped substantially below support, the correct answer is
**`WAIT FOR RETEST`** rather than shorting the bottom.

### The worked example — what "convincing" means

Resistance at $100. Prior candles close 97.8, 98.9, 99.3, 99.7 — pressing the level.

```
Breakout candle          Volume
Open   99.4              20-candle average   1.2M
High  102.8              breakout candle     2.4M
Low    99.2
Close 102.2
```

Closes 2.2% clear of the level on **2× average volume**. That is substantially more
convincing than:

```
High  101.00
Close  99.50
```

which merely **wicked through resistance and closed back underneath** — a potential false
breakout.

### The case this strategy exists to reject — false breakouts

```
          $103
            │
            │ wick
            │
$100 ───────┼──────── Resistance
           █│
           █│
           ██
         close $98.8
```

Price technically traded above $100 and could not hold there. The system should read this as
a **failed breakout / potential liquidity sweep** and **not LONG on the resistance breach
alone**. Depending on the broader structure, a *failed bullish breakout is itself evidence
for a SHORT*.

The material lists five false-breakout signals: lack of volume, weak momentum, price
reversal, candlestick patterns, and potential market manipulation.

**The fifth is explicitly out of scope.** From OHLCV alone the system cannot know that
manipulation *caused* a move, and it must not claim so. It reports **observable behaviour
consistent with** a failed or swept breakout:

```
Resistance $100 · Open 99 · High 104 · Close 99.30
```

A long upper wick closing back under the level is a fact. "Whales manipulated the price" is
not. The output is **"breakout failed / rejection detected"**, never an intent claim.

### Three states around a level

This replaces the binary broke/did-not-break reading, and is the labelling the whole family
hangs off:

| State | Condition | Meaning |
| --- | --- | --- |
| ① | Price **closes above** the level | **Breakout candidate** |
| ② | Price **stays above**, and/or successfully **retests** | **Confirmed breakout** |
| ③ | Price breaks above but **closes / returns below** | **False breakout** |

### False-breakout detection is a filter, not a strategy

Recorded as a decision: it is **not** a seventh setup initially. It is a **risk / filter
module** applied to the others:

```
Potential LONG breakout  →  false-breakout risk: HIGH  →  do not LONG
```

The reasoning is the BitFunded challenge: **avoiding a bad trade can be worth more than
finding another trade.**

---

## Strategy 3 — Breakout + retest

Kept as its own strategy rather than folded into #2, because the sequence is the setup. For
a selective system the trader **prefers 3 over 2** where both are available: protecting
drawdown matters more than catching every move.

### The sequence — order is the specification

```
1. KEY LEVEL EXISTS
        ↓
2. BREAKOUT
        ↓
3. CONFIRM BREAKOUT QUALITY
        ↓
4. PRICE RETURNS TO BROKEN LEVEL
        ↓
5. LEVEL HOLDS
        ↓
6. CONFIRMATION CANDLE
        ↓
7. MOMENTUM CONFIRMS
        ↓
8. R:R CHECK
        ↓
   LONG / SHORT
```

A checklist of conditions met **in any order** does not express this — steps 2 and 4 are the
same level read at different times, and step 5 only means anything after step 4.

### Strategy 3A — Bullish breakout + retest

```
                         ↑
                        / \
                       /   \
$100 ─────────────────●────── Resistance
                    /   \
                   /     ● ← RETEST
                  /      ↑
           BREAKOUT    support holds
                          \
                           ↑
                         LONG
```

- **Before the breakout:** $100 was resistance and had **meaningful prior reactions**
- **Breakout:** the candle **closes above** $100 rather than merely wicking above it;
  ideally volume expands and momentum is healthy
- **Retest:** price **returns toward ~$100**
- **Confirmation:** former resistance **acts as support** — buyers reject lower prices, and
  price does **not decisively close back below** the level

Then evaluate **LONG**.

### Strategy 3B — Bearish breakout + retest

The mirror. Old **support becomes resistance** after the breakdown; a bearish rejection at
the reclaimed level gives the potential **SHORT**.

```
                         SHORT
                           ↓
                          /
                         ●
                        /
$100 ─────────────────●──────── Support
                    /  ↑
                   / RETEST
                  /
                 ↓
              BREAKDOWN
```

### Aggressive versus conservative, stated plainly

| | Entry | Trade-off |
| --- | --- | --- |
| **#2** breakout | On the strong close through the level | Earlier entry, **greater false-breakout risk** |
| **#3** breakout + retest | After the level holds on the return | Later entry, **drawdown protection** |

---

## Recorded for the design step — not decided here

### Measurements the breakout conditions need

Recorded by the trader as the objective quantities the level-based setups are read from.
Everything here is arithmetic over the closed indicator set — no judgement, no thresholds
baked into the measurement itself:

| Group | Measurement |
| --- | --- |
| Level geometry | breakout distance / ATR · close distance from level · retest distance / ATR |
| Candle shape | upper wick / range · lower wick / range · body / range |
| Volume | breakout volume / 20-period average volume |
| Momentum | MACD histogram · MACD histogram **change** · Stoch RSI %K · %D · **%K−%D** |
| Trend | EMA10 · EMA21 · EMA50 · SMA200 · **EMA slopes** |
| Volatility | ATR · volume |

### These fit inside the closed indicator set

Checked 2026-08-20 against the closed list above — **strategies 2 and 3 need no new
indicator.** Levels come from raw candles via the existing swing pivots; wick/body ratios are
candle geometry; `VOLUME_WINDOW` is already 20, so `relative_volume` *is* "breakout volume /
20-period average"; MACD histogram, Stoch RSI and ATR are on the list. EMA slopes and MACD
histogram change are derivatives of listed values, not new inputs.

### Existing code these would build on

- **`retest.py`** classifies a candle window as `none` / `broken` / `held` / `failed` — the
  break → return → hold sequence, which maps onto states ①②③ and onto steps 2-5 of the #3
  sequence. It is currently **unwired**: nothing in `src/` imports it since the `entry.*`
  flags were retired, and only its unit tests keep it alive. It is raw material for #3, not
  dead weight.
- **`movers`** scores relative strength vs BTC, breakout position, volume expansion and
  acceleration. It is a live CLI command, but it produces a **0-100 score** — the shape the
  catalogue moved away from — and `backtester.py` never references it, so **its edge is
  unknown**.
- **`patterns.py`** already measures wick and body geometry for its pin-bar predicate, which
  is the same arithmetic the false-breakout test needs.

### Open questions

1. ~~**What makes a level "established"?**~~ **Answered** by `min_touches` /
   `level_tolerance_atr` — as a default, not a finding. See the table above.
2. ~~**What counts as "convincingly above"?**~~ **Answered** by `min_break_atr` and
   `min_body_ratio`, again as defaults.
3. **How long may a retest take?** Still open, and it belongs to Strategy 3 — step 4 has no
   time bound, and a return three bars later and thirty bars later are different setups.
   `break_lookback` bounds how old the *break* may be, not how long the retest may take.
4. ~~**Do 2A/2B need a direction rule at all?**~~ **Answered: no.** Direction comes from the
   level's own kind — resistance can only break upward, support only downward. Taking it
   from the stack would reintroduce exactly the gate this family is meant not to have.
5. ~~**How should a coin presenting more than one setup be reported?**~~ **Answered:**
   every match is carried and shown; the deciding one is arbitrated by `primary_verdict`,
   and a direction conflict trades nothing.
6. **Deterministic or judged?** Per this file's header, that split is still undecided.

---

## What the engine has today, against 1A/1B

Re-recorded **2026-08-20** by reading the code, after the catalogue was built. The
2026-08-19 audit that stood here recorded six of these eight rows as *Missing* or
*Partial*; the rows below replace it. The checklist is evaluated on the **reference**
timeframe, so the verdict and the trade plan always describe the same trade.

| Rule | Status | Where |
| --- | --- | --- |
| EMA10 / EMA21 / EMA50 / SMA200 | **Present** | `indicators.py` computes all four; the old EMA20/50/200 set is gone |
| HH + HL / LH + LL | **Present** | `structure.py` classifies exactly this per timeframe |
| Trendline (rising / falling) | **Present** | `trendline.py` fits a line to the swing pivots — default 3 touches, r² ≥ 0.7, and price must be within 1.5 ATR of it |
| EMA21-EMA50 retracement zone | **Present** | `zone_bounds` is the band itself. Extension is **signed in the trade's direction** — a long *below* the band has overshot the pullback, not run away — with a 2.0 ATR limit |
| Candlestick reaction patterns | **Present** | `patterns.py` detects six, from three direction-aware predicates (engulfing, pin bar, star): bullish/bearish engulfing, hammer, shooting star, morning/evening star. Positional — the pattern must be *at* the zone |
| Stoch RSI extreme + cross | **Present** | The **cross event** within 3 bars, and the extreme %K came *from* on its way in (0.20 / 0.80). Not a position test |
| MACD crossover / zero line | **Present** | The method's "and/or" is kept as an and/or: either satisfies, and the detail says which. A gate, not a score input |
| Two-question output | **Present** | `SetupVerdict` carries `trend`, `strategy`, `entry_status`, `decision` and `reason` as separate fields |

`strategies.enabled` defaults to **true**, and is on in both `config.example.yaml` and
`mywatch.yaml` — so the shipped behaviour *is* the above. The 0-100 quality score it
replaced has been retired along with its eight scored categories.

**What it measures to.** Implemented to specification is not the same as profitable. Over
86 symbols, 2022-01-01 → 2026-07-31: **PF 0.979, -0.0146R expectancy, 61.9% max drawdown**
across 1,121 trades. The checklist removes 79% of the trades the engine would otherwise
take and improves expectancy by +0.293R against its own control — a large effect, and still
below break-even. See `results.md` for the full table and the reasoning; the failure is the
hit rate, not the risk model.

## What the engine has today, against 2A/2B

Recorded **2026-08-20**, at the point the breakout evaluator landed.

| Rule | Status | Where |
| --- | --- | --- |
| Established level | **Present** | `breakout.find_levels` clusters swing pivots within `level_tolerance_atr` into one level; `min_touches` (default 2) is what makes it established |
| Close convincingly beyond | **Present** | A *close* past the level by `min_break_atr` — the high reaching through is explicitly not a break |
| Not just a wick | **Present** | Body must be ≥ `min_body_ratio` of range **and** exceed the rejection wick, so a candle pushed back fails on shape even with a healthy body |
| Volume expands | **Present** | Breaking bar against the same 20-bar window `relative_volume` uses |
| Structure supports continuation | **Present** | Deliberately weak: fails only when structure points the *other* way, since the table rates it context and a range break often ends a sideways structure |
| Room to the next level | **Present** | The honest form of the R:R test — the planner can floor a target, it cannot manufacture space |
| Not already extended | **Present** | Past `max_extension_atr` the answer is **wait for the retest**, not "no setup" |
| Three states ①②③ | **Present** | `BreakoutState.CANDIDATE` / `CONFIRMED` / `FAILED`, plus `NONE` |
| False-breakout filter | **Present**, as specified | Not a condition and not a setup: a `FAILED` reading fails the break row and says the level *held* — "evidence for the level, not against it" |

**Wired 2026-08-20, with multiple verdicts per coin.** The two families are judged
independently and a coin carries one verdict per family that matched:

- `CoinAnalysis.verdicts` holds every match; `.verdict` is the one deciding the row.
- **`runner.primary_verdict` is the arbitration rule.** One ready setup, or several agreeing
  on direction, is taken. Several ready and *disagreeing* on direction takes **neither** —
  a coin one method reads long and another reads short is not a strong signal, it is an
  unclear one, and picking a winner by rank would manufacture confidence the evidence does
  not support. With nothing ready, the checklist nearest to complete decides, because that
  is the most informative WAIT.
- `CoinAnalysis.setup_direction` is the direction actually traded. A breakout resolves its
  own from the level it broke, so the runner **plans that trade too** — otherwise the
  family's defining case, a level breaking with no trend behind it, could never surface for
  want of levels to quote.
- The table's Setup column shows every match (`1A+2A`); the analyst brief carries every
  verdict with the deciding one marked; the backtester evaluates both families so the
  breakout can be measured rather than permanently unproven.
- The analyst prompt now states 2A/2B, the break-versus-confirmed-breakout distinction, the
  "the level held" wording for failures, and that several verdicts must be weighed together.

`breakout.enabled` still ships **false**: the family has never been replayed over history,
and this repository does not ship unmeasured strategies on. Wiring makes it measurable; it
does not make it proven.

### Thresholds chosen, and on what basis

Three of the five open questions below needed an answer to write the code at all. These are
**defaults, not findings** — every one is a knob, and a backtest may move it:

| Knob | Default | Why that value |
| --- | --- | --- |
| `min_touches` | 2 | The material says a level is "not one touch"; two is the smallest number that satisfies it |
| `level_tolerance_atr` | 0.5 | A level is a band, not a number; half an ATR is narrow enough to keep distinct levels apart |
| `min_break_atr` | 0.25 | Separates a break from a touch at the prevailing volatility rather than by a fixed percentage |
| `min_body_ratio` | 0.5 | The candle must be more body than not |
| `volume_multiple` | 1.5 | **The method's own figure.** The material notes 1.2 may suffice in some regimes — unmeasured, hence a knob |
| `min_room_atr` | 2.0 | Matches `target_rr`: space for the 1:2 the planner will floor to anyway |
| `max_extension_atr` | 2.0 | Mirrors the pullback family's chase limit, so "too far" means the same thing in both |
| `break_lookback` | 3 | Matches the Stoch RSI cross window — a break 20 bars old is history |

## What the analyst can see today

The 2026-08-19 constraint — "the model cannot evaluate 1A or 1B regardless of prompting,
the inputs are absent" — is closed. The evidence pack now carries:

- **The engine's own checklist read** (`VerdictBrief`): trend, strategy, entry status,
  decision, reason, and **every condition with the measurement behind it** — so where the
  engine says WAIT the model can see which condition failed and by how much.
- **The method's indicator values per timeframe** (`TimeframeBrief`): `ema10`, `ema21`,
  `ema50`, `sma200`, `macd`, `macd_signal`, `stoch_rsi_k`, `stoch_rsi_d`, the `stoch_cross`
  and `macd_cross` events, `reaction_patterns` and `trendline` — the values themselves, not
  a `mixed` label.
- **Recent raw candles** for the decision timeframe (30 by default), so the model can read
  price action the pattern predicates do not cover.

The prompt is no longer silent on strategies: it states 1A and 1B in full, including the
correction that a short **rejects from resistance** rather than bouncing off support, and
that a trend existing is not an entry. The model returns its *own* `strategy` and
`entry_status`, which are shown beside the engine's rather than replacing them — where they
disagree, the trader sees both. Near-misses are reviewed too (`review_within`, default 2),
because a condition about to complete is where a model is most useful.

**The design question is answered, for 1A/1B.** The app computes the seven conditions and
hands them over as facts *and* sends raw candles — both, not either. The engine's read is
the anchor; the candles are what lets the model disagree with it. Strategies 2 and 3 are
still unspecified, and may need evidence these two do not.
