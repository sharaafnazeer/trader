# Strategy Catalogue — measured results

The catalogue is the trader's own method, implemented to specification. This file records
what it did when replayed over history — **including if that is nothing good**. A feature
that ships an honest null result is worth more than one that ships a number nobody checked.

## What is being measured

The checklist gates the backtest exactly as it gates the live scan: one evaluator, the same
reference-timeframe inputs, a trade only where the verdict reads READY. That equivalence is
pinned by `tests/test_backtest_checklist.py`, and it is what makes these numbers describe
the strategy the scanner actually runs rather than a looser one that merely resembles it.

| | |
| --- | --- |
| Config | `mywatch.yaml` — 86 symbols |
| Window | 2022-01-01 → 2026-07-31 |
| Timeframes | 15m / 1h / 4h / 1d, reference 4h |
| Costs | fee 0.04%/side, slippage 0.05%, risk 1% of equity per trade |
| Strategies | 1A / 1B, all seven conditions, shipped defaults |

## The prior engine, for comparison

The 0-100 quality score this replaced, over the same window and watchlist:
**1,362 trades, 35.8% win rate, +0.065R expectancy, PF 1.10, 75.1% max drawdown.** Thin and
not tradeable as it stood — which is why the method replaced it.

That figure is *not* a like-for-like baseline. The engine underneath it changed: different
moving averages, a different direction rule, a different surfacing test. Treat it as "what
the previous thing did", not as a control.

## Measured 2026-08-20 — **the checklist helps substantially, and still loses money**

Both runs: identical code, watchlist, window and costs. The only difference is
`strategies.enabled`.

| Metric | Checklist ON | Control (OFF) | Change |
| --- | ---: | ---: | --- |
| Resolved trades | 1,121 | 5,226 | −4,105 (79% filtered out) |
| Win rate | 33.9% | 32.0% | +1.9pp |
| Expectancy | **-0.0146R** | -0.3080R | **+0.2934R** |
| Profit factor | **0.979** | 0.683 | +0.296 |
| Max drawdown | 61.9% | 100.4% | -38.6pp |
| Avg win | +2.008R | +2.076R | -0.068R |
| Avg loss | -1.052R | -1.429R | +0.377R |

### What this says

**The checklist does a lot of work.** It removes 79% of the
trades the engine would otherwise take and improves expectancy by **+0.293R
per trade**. Without it the same engine is not merely unprofitable, it is ruinous: profit
factor 0.68 and a **100% drawdown**, which is the account gone. Whatever
else is true, the seven conditions are filtering for something real.

**It is still not profitable.** Profit factor 0.979 is below 1.0; expectancy
-0.0146R means each trade cost about 1.5% of the amount risked. Over
1,121 trades at 1% risk that is roughly -16% of starting equity,
with a 62% peak-to-trough fall on the way. **This is not tradeable as it stands.**

### The failure is the hit rate, not the risk model

The payoff geometry is exactly what was specified — avg win +2.008R against avg loss
-1.052R, the 2:1 the plan floors every target to. At that payoff:

| | Break-even win rate | Actual | Short by |
| --- | ---: | ---: | ---: |
| Checklist ON | 34.4% | 33.9% | **0.48pp** |
| Control OFF | 40.8% | 32.0% | 8.79pp |

The method is **0.48 percentage points of win rate** from breaking even —
about 5 trades out of 1,121. That is a very different
situation from "the idea is wrong", and it points at the entry conditions rather than at the
stops or targets.

One further observation, offered as a lead rather than a finding: the average **loss** improved
from -1.429R to -1.052R. A loss worse than −1R means price left the stop behind —
a gap, or a violent bar. Entering at a structurally sound level appears to produce cleaner
stop-outs, which is a mechanism worth understanding before tuning anything.

### What was NOT measured

- **No threshold was tuned.** These are the shipped defaults, measured once. The obvious next
  step — sweeping `max_extension_atr`, `stoch_oversold`, `cross_lookback` — is also the obvious
  way to overfit seven conditions to one window, and it was deliberately not done here.
- **No per-condition attribution.** Which of the seven does the filtering, and which merely
  costs trades, is unknown. Turning them off one at a time is now cheap (~12 minutes a run).
- **The comparison is against no-checklist, not against the retired score.** The old
  +0.065R / PF 1.10 figure came from a different engine — different moving averages, a
  different direction rule — and is not a control.


## Standing caveats

- One window, one watchlist, one set of thresholds. Seven conditions with a dozen tunables
  between them is a large space to fit; a good number here is a hypothesis, not a finding.
- Every trade is a 4h-reference entry with a structural stop and a 2R-floored target. The
  measurement says nothing about other timeframes or other exits.
- Nothing here is forward-tested. A historical result is not a promise.
