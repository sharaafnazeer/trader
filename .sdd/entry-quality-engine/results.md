# Entry-Quality Engine — measured results

Every decision-changing flag in this feature is measured against a saved baseline before its
shipped default is set. This file records what each measurement found, **including the ones
that argued against the change**. A feature that ships three improvements and honestly
reports two duds is worth more than one that ships five and cannot say which mattered.

## Measurement setup

| | |
| --- | --- |
| Config (control) | `bench-slow.yaml` — 20 liquid crypto majors |
| Config (variant) | `bench-plateau.yaml` — identical, one flag flipped |
| Window | 2022-01-01 → 2026-07-31 |
| Costs | fee 0.04%/side, slippage 0.05%, risk 1% of equity per trade |
| Harness | `backtest --json` then `backtest --baseline`, comparability enforced on window + costs |

**Note on the control.** The 20-coin list is an *iteration* config, not a recommendation. A
walk-forward test (see below) showed that selecting coins by past performance does **not**
reliably beat a random 20-coin subset, so this list must not be read as "the good coins". It
is used only because the variant uses the identical list — the flag is the sole difference.

## `entry.rsi_plateau` — measured 2026-08-19 — **SHIPS OFF**

Replaces the momentum category's linear RSI credit (`rsi/100`, so RSI 90 earned 0.9 toward a
long) with a plateau: full credit through RSI 50-70, ramping below, tapering to 0.25 at 100.

| Metric | Control | Plateau | Change | |
| --- | ---: | ---: | ---: | --- |
| Resolved trades | 563 | 624 | **+61** | — |
| Win rate | 39.1% | 38.6% | −0.5pp | WORSE |
| Expectancy | +0.1565R | +0.1386R | **−0.0179R** | WORSE |
| Profit factor | 1.251 | 1.220 | −0.031 | WORSE |
| Max drawdown | 40.9% | 48.4% | **+7.5pp** | WORSE |

Average win and loss barely moved (1.998/−1.024 → 1.988/−1.025), so the damage is not in how
trades resolved — it is in **which** trades were taken.

**Why it failed.** The +61 extra trades are the tell. The curve reaches full credit at RSI 50,
which makes it *more* generous below the healthy band than the formula it replaced: at RSI 40 it
awards 0.8 where the old curve awarded 0.4. That lifted mediocre setups over the quality
threshold. It promoted more weak setups than it suppressed extended ones, and drawdown rose
7.5 points as a result.

This was predicted before the run, from bucketing all 1,362 historical trades by their 4h RSI
at entry:

| RSI band at entry (directional) | n | win% | expectancy |
| --- | ---: | ---: | ---: |
| weak (≤50) | 10 | 0.0% | −1.0331R |
| healthy (50–70) | 796 | 36.8% | +0.0938R |
| extended (70–85) | 521 | 34.0% | +0.0098R |
| very extended (>85) | 35 | 51.4% | +0.5312R |

The aggregate supports penalising extended entries (+0.0427R extended vs +0.0798R
non-extended), which is why the premise looked sound. But the curve as specified gets the two
tails wrong: it rewards the worst-performing band (≤50) more than before, and punishes the
best-performing band (>85) hardest.

**Candidate refinement, not a claim:** keep the sub-50 ramp at its original slope, and taper
only across 70–85 rather than all the way to 100. That is a *new hypothesis* and needs its own
measurement — the tails that motivate it hold 10 and 35 trades, and fitting a curve to those is
the overfitting that sank the coin-selection idea below.

## `entry.retest` — **NOT YET MEASURED** — ships off

Scores a breakout as a sequence (level broken, price returning, the level holding or
failing) rather than one close against one swing pivot. A failed retest earns **no**
breakout credit, a held retest earns full credit, and the two states that say nothing about
a retest leave the existing price-derived credit alone.

The iteration-config run (`bench-retest.yaml` vs the saved `bench-slow.yaml` control, same
window and costs) was started on 2026-08-19 and **stopped before completion** at the user's
instruction — roughly 50 minutes of compute that task 07 will spend anyway when it measures
every flag over the full window.

So there is **no number here in either direction**. The flag ships off because that is the
feature's rule for anything unmeasured, not because it was tried and found wanting. Its
implementation is complete and unit-tested; only the edge is unknown.

## `entry.zones` — **NOT YET MEASURED** — ships off

Replaces the plan's single entry price with a zone around the reference timeframe's EMA20
(`zone_atr` = 0.5 ATR either side), reports whether the current price is inside it
(`actionable`), states the trigger in prices, and computes risk, the take-profit floor and
`risk_reward` from the zone edge that would fill.

**The simulator changed, deliberately, before any number was produced.** The backtest used to
assume a fill at the reference close. Moving the entry to a zone below that close without
changing the simulator would have filled every long at a better price the market may never
have printed *and* shrunk risk to the zone-plus-buffer distance — both inflate R-multiples, so
the delta would have looked excellent and meant nothing. Entries with a zone are therefore
**pending limit orders**: the trade exists only if a later bar trades into the zone within
`zone_fill_bars` bars of the finest timeframe, the recorded fill is the zone edge, and an
order that expires unfilled records *no trade* (not an unresolved one). A plan that is not
yet actionable is not skipped — it rests as that pending order.

**What is already known without the full-window run:** on the deterministic fixture, zones-on
takes **zero** trades where flags-off takes 16. The reason is structural rather than a bug —
this engine surfaces a coin *after* a confirmed run, when price sits several ATR above its
EMA20, so a half-ATR zone at the EMA20 is rarely reached within a day of finer bars. Expect a
large fall in trade count on real data too, and read any expectancy improvement in that light:
a handful of trades is not evidence.

No iteration-config comparison was run (the ~50-minute runs were stopped as unnecessary at
this stage), so there is **no delta in either direction**. The flag ships off because it is
unmeasured, and task 07 owns the measurement.

## `entry.quality` — **NOT YET MEASURED** — ships off

Splits the single 0-100 verdict in two. The trend score keeps its meaning and its weights
untouched; a second **entry** score (extension from EMA20 35, Stochastic RSI turn 25,
retest state 20, reward-to-risk from the fill edge 20) returns ACTIONABLE or WAIT. A WAIT
carries the blockers behind it — each naming a measurable quantity *with its value*,
"extended 4.1 ATR above EMA20 (limit 3.0)" — and the trigger that would clear them. The
surfacing decision is gated on the verdict in **both** the live path and the backtest
evaluation path, so a WAIT suppresses the trade rather than annotating it.

**One consequence is already known, and it is the opposite of the intuitive one.** On the
deterministic fixture, flags-off takes **16** trades and quality-on takes **55 resolved**
(plus one still open at the end of the history). The trade count goes *up*, not down.

The mechanism is the lifecycle, not the scorer. The backtest enters on the **rising edge**
of `surfaced`, so a signal that qualifies continuously for forty bars produces one trade.
Gating `surfaced` on the entry verdict interrupts those runs: qualify, wait, re-qualify —
and each re-qualification is a fresh rising edge. The gate suppresses individual bad
moments (verified: at `UPCOIN` t=1612800000 the flag-off run opens a trade and the flag-on
run does not, blocked by "extended 3.1 ATR below EMA20") while *fragmenting* persistent
signals into more entries overall.

Whether that is good or bad is exactly what the measurement is for, and it is not obvious
either way: more, better-timed entries could raise expectancy, or the extra trades could be
the same churn that sank `rsi_plateau`. Read any full-window trade-count change with this
mechanism in mind rather than as evidence the filter "let more through".

**No iteration-config comparison was run.** Following the same decision recorded for
`entry.retest` and `entry.zones` above — the ~50-minute-per-side runs were judged not worth
spending when task 07 measures every flag over the full window anyway — there is **no delta
here in either direction**. The flag ships off because it is unmeasured, not because it was
tried and found wanting. Its implementation is complete and unit-tested; only the edge is
unknown.

**Not delivered, and recorded as such:** user story 4 ("setups I would recognise as textbook
pullbacks surface"). Entry quality can only re-label setups that already cleared
`quality_threshold`, so the motivating BOMEUSDT case (score 51.7, rank 42 of 51) never
reaches the entry scorer. Changing what surfaces is a separate measured question.

## Coin selection — measured 2026-08-19 — **NOT SUPPORTED**

Tested because a 20-coin subset appeared to double expectancy and halve drawdown versus the
87-coin watchlist. Both effects evaporated under proper testing.

- **Drawdown was subset size, not coin quality.** Random 20-coin subsets had a median drawdown
  of 33.4%; the hand-picked 20 sat at 40.9%, the **16th percentile** — worse than 84% of random
  subsets.
- **Expectancy was selection bias.** The hand-picked 20 sat at the 94th percentile of random
  subsets, whose median expectancy (+0.0641R) equals the full watchlist's.
- **A structural rule gains nothing.** Dropping equities and commodities (68 coins, zero
  performance input): +0.0706R vs +0.0646R, drawdown unchanged at 75.1%.
- **Walk-forward refutes it.** Selecting the top 20 by past expectancy and testing out of
  sample gave percentiles of **98, 16, 85, 67** across four splits — one split *worse* than
  random — and worse drawdown than random in 3 of 4.

**What is real:** trading fewer coins concurrently cuts drawdown roughly in half (75% → ~33%
median for *any* 20 coins). That is about concurrent correlated exposure, not coin quality, and
points at capping simultaneous positions rather than curating the watchlist.

## Faster timeframes ("3-4 trades a day") — measured 2026-08-19 — **NOT SUPPORTED**

Tested because the goal was 3-4 trades a day rather than one every three days. Moving the
lead timeframe from 1d to 4h and the reference from 4h to 1h, same 20 coins, same window,
same costs — only the timeframe set changed.

| Metric | Control 4h/1d | Fast 15m/1h/4h | |
| --- | ---: | ---: | --- |
| Trades | 563 | 2,830 | +2,267 |
| Trades/day | 0.34 | **1.69** | still below the 3-4 target |
| Win rate | 39.1% | 34.0% | WORSE |
| Expectancy | +0.1565R | **−0.0356R** | WORSE — negative |
| Profit factor | 1.251 | **0.949** | WORSE — below 1.0, i.e. loses money |
| Max drawdown | 40.9% | **89.4%** | WORSE |

At 0.25% risk on $5,000 the control earns **+$0.66/day** and the fast profile **−$0.75/day**.

**Why.** Average win and loss barely moved (1.998/−1.024 → 1.947/−1.056) while win rate fell
5.1 points: the reward-to-risk geometry survived, the hit rate did not. Tighter stops are hit
more often, and friction scales against you — a 1h stop gives back ~13.6% of R to fees and
slippage against ~6.0% at 4h. The edge lives in the higher timeframes and dies below them.

Reaching 3-4 trades/day would need 5m data — faster stops again, worse friction again, the
direction that just failed. **The engine's best measured configuration is the slow one.**

## Regression check — 2026-08-19

With every entry flag off, the full 87-coin backtest over 2022-01-01 → 2026-07-31 reproduces
the recorded baseline exactly: **1,362 trades, 35.8% win rate, +0.0646R, PF 1.099, 75.1% max
drawdown** (recorded: 1,362 / 35.8% / +0.065R / 1.10 / 75.1%). This held across changes to
`runner.py`, `market_data.py`, `scoring_model.py`, `backtester.py`, `brief.py`, `config.py` and
`metrics.py` — the flags-default-off discipline is verified, not asserted.
