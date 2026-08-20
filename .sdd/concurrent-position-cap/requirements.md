# Concurrent Position Cap

## Problem Statement

The engine's measured maximum drawdown is **75.1%** — a hole requiring a 4× gain to climb out
of, and the single reason the strategy is not tradeable despite a positive expectancy. Every
attempt to improve the edge this week failed measurement; this is the one lever that did not.

The cause is now measured rather than suspected. Filtering the 1,362-trade history to random
20-coin subsets and recomputing each equity curve gives a **median drawdown of 33.4%** against
the full watchlist's 75.1%. The hand-picked 20-coin list sits at 40.9% — the *16th* percentile,
worse than 84% of random subsets. In other words the improvement has nothing to do with which
coins are held and everything to do with **how many positions are open at once**. Walk-forward
testing separately refuted coin selection as a lever (out-of-sample percentiles of 98, 16, 85,
67 across four splits, one worse than random), so curating the list is not the answer.

The mechanism is visible in live output. A single scan surfaced **44 short setups against 7
longs**. Those are not 44 independent bets; on a market-wide bounce they are one bet, sized 44
times. Correlated same-direction exposure is how a thin edge becomes a catastrophic drawdown,
and nothing in the system currently limits it.

Nothing does, because nothing *can*. The backtester walks one coin at a time from start to
finish and merges the results into a flat list — each coin is simulated in complete isolation,
with no notion of what else was open at that moment. There is no portfolio state to cap. The
live path is worse: it tracks no open positions whatsoever, so it cannot know whether the
trader is already carrying twenty of these.

## Solution

Make the backtest **portfolio-aware**, then cap how many positions may be open at once.

Instead of simulating each coin independently, the replay advances through time
chronologically and, at each decision point, considers every coin together. When more setups
qualify than there are free slots, the cap admits the best of them and declines the rest. When
positions close, slots free up. This turns the backtest into something that can answer "what
if I never hold more than N at a time?" — a question it currently cannot express.

The cap is expressed in two ways, because they control different risks. A **total** limit bounds
overall exposure. A **per-direction** limit bounds the specific failure mode that produced the
drawdown: a basket of same-direction bets on assets that move together. A trader holding six
positions, three long and three short, is carrying far less correlated risk than one holding
six shorts, and only the second is what 75% drawdowns are made of.

The engine also gains an honest answer to "which of these do I take?". Today it surfaces
everything above a threshold and leaves the choice unstated; with a cap it must rank and
choose, and the ranking rule is a measured decision rather than an accident of iteration order.

The whole behaviour sits behind configuration that defaults to unlimited — today's exact
behaviour — and is measured against the recorded baseline before any default changes. The
prediction to be tested is specific: capping concurrent positions should cut drawdown roughly
in half at a modest cost in expectancy, because it declines some winning trades along with the
correlated losers. A cap that reduces drawdown *and* holds expectancy would be the first
unambiguous improvement found; a cap that destroys expectancy would be honestly reported as
such.

For the live scanner the cap is advisory only. The application does not track open positions
and this feature does not add that — so live, it reports how many of the surfaced setups would
fit within the configured cap and which ones it would choose, leaving execution to the trader.

## User Stories

1. As a trader, I want to limit how many positions the strategy holds at once, so that a market-wide move cannot lose on twenty correlated bets simultaneously.
2. As a trader, I want a separate limit on positions in the same direction, so that a basket of correlated shorts is recognised as one bet rather than many.
3. As a trader, I want to know the measured effect of each cap on drawdown, so that I can choose a limit on evidence rather than instinct.
4. As a trader, I want to know what each cap costs me in expectancy, so that I can judge the trade-off rather than only the benefit.
5. As a trader, I want the backtest to model the cap honestly, so that the reported drawdown is what the cap would actually have produced.
6. As a trader, I want the engine to tell me which setups it would choose when more qualify than there are slots, so that the choice is explicit rather than left to me at the moment of least patience.
7. As a trader, I want the ranking rule behind that choice to be stated, so that I know whether it is picking by score, by direction balance, or by something else.
8. As a trader, I want the cap disabled by default, so that my existing results stay reproducible until I decide to change them.
9. As a trader, I want the scan to report how many of today's setups would fit within my cap, so that the limit is visible at the moment I am deciding what to do.
10. As a trader, I want to see which setups the cap would decline and why, so that a declined setup is a stated decision rather than a silent omission.
11. As a trader, I want the AI analyst to know how many slots are free, so that it can weigh a marginal setup against the opportunity cost of filling the last one.
12. As a trader, I want a cap of one to be expressible, so that I can test the extreme case of never holding more than a single position.
13. As a trader, I want to see the distribution of how many positions were open over the backtest, so that I know whether my cap ever actually binds.
14. As a trader, I want to know how often the cap declined a setup that would have won, so that I can see what the protection cost.
15. As a maintainer, I want the chronological replay to produce identical results to the per-coin replay when no cap is set, so that the restructuring is provably behaviour-preserving.
16. As a maintainer, I want the portfolio state to be a pure, testable value rather than hidden mutable state, so that slot accounting can be asserted directly.
17. As a maintainer, I want the ranking rule to be a pure function, so that its behaviour under ties and equal scores is exactly specified.
18. As a maintainer, I want the new modules free of terminal-rendering dependencies, consistent with the rest of the codebase.
19. As a trader, I want the momentum scanner unaffected unless the cap is explicitly extended to it, so that improving one engine does not silently alter the other.
20. As a trader, I want the measured results published with the window, watchlist and cost assumptions, so that the measurement can be repeated.

## User Acceptance Tests

1. Given the cap is unset, when a backtest is run over a fixed window, then the reported trade count, win rate, expectancy, profit factor and drawdown match the recorded baseline exactly.
2. Given the cap is unset, when a scan is run, then its output is unchanged from the pre-existing behaviour.
3. Given a total cap of three and a moment at which five setups qualify, when the backtest processes that moment, then exactly three positions open and two are declined.
4. Given a total cap of three and three positions already open, when a further setup qualifies before any position closes, then no fourth position opens.
5. Given a total cap of three and three positions open, when one closes, then the next qualifying setup may open.
6. Given a per-direction cap of two and a moment at which five short setups qualify, when the backtest processes that moment, then at most two short positions open regardless of the total cap.
7. Given a per-direction cap of two and two shorts open, when a long setup qualifies, then it may open — the short limit does not block the opposite direction.
8. Given more qualifying setups than free slots, when the cap chooses between them, then the chosen ones are those the stated ranking rule selects, verified against a hand-built moment with known scores.
9. Given two setups with identical scores competing for one slot, when the cap chooses, then the choice is deterministic across repeated runs over the same data.
10. Given a cap of one, when a backtest is run, then at no point in the resulting trade log do two trades overlap in time.
11. Given any completed backtest with a cap set, when the report is inspected, then it states how many setups were declined by the cap.
12. Given any completed backtest with a cap set, when the report is inspected, then it states the distribution of concurrently-open positions over the run.
13. Given a completed backtest with a cap set, when the report is inspected, then it states how many declined setups would have been winners.
14. Given a cap is configured, when a scan is run, then the output states how many surfaced setups fit within the cap and which would be declined.
15. Given a cap is configured and the AI analyst is enabled, when the evidence is built, then it states how many slots the cap leaves free.
16. Given the momentum scanner, when it is run with a cap configured for the trend engine, then its output is unchanged.
17. Given a cap value of zero or a negative number in configuration, when the configuration is loaded, then it is rejected with an error naming the setting.
18. Given the published results, when they are read, then each cap value's measured trade count, win rate, expectancy, profit factor and drawdown are stated alongside the window, watchlist size and cost settings used.

## Definition of Done

- All user acceptance tests pass.
- Both caps default to unlimited, and with them unset the backtest reproduces the recorded baseline exactly and the scan output is unchanged.
- The chronological replay is proven equivalent to the per-coin replay when no cap is set, by a test comparing both over the same fixture.
- A measured comparison table covers a meaningful range of cap values, reporting drawdown *and* expectancy for each, over a stated window and watchlist.
- The shipped default for each cap reflects what that table justifies, and the numbers are recorded whether or not they favour capping.
- The report states, for any capped run, how many setups were declined, the distribution of concurrent positions, and how many declined setups would have won.
- The ranking rule used to choose among competing setups is documented and its behaviour under ties is specified.
- No claim that drawdown is improved appears without the measured figures and the corresponding expectancy cost beside it.
- The new analysis modules import no terminal-rendering libraries.
- The full pre-existing test suite passes unchanged and the project's quality gates are green.
- Documentation explains both caps, their measured effects, their defaults, and that live enforcement is advisory only.

## Out of Scope

- **Live position tracking and enforcement.** The application does not know what the trader holds, and this feature does not add it. Live, the cap is advisory: the scan reports what would fit. Actual tracking is a separate, larger feature.
- **Order execution.** Unchanged: advisory only.
- **Position sizing.** How large each position is remains out of scope; this feature governs how *many*.
- **Correlation measurement.** The per-direction cap is a deliberately crude proxy for correlation. Measuring actual pairwise correlation and capping on that is a plausible successor, not this.
- **Applying the cap to the momentum scanner.** The trend engine first; extending it is a follow-up once measured.
- **Re-tuning the quality threshold, category weights or any entry-quality flag.** Held fixed so the cap's measured effect is attributable to the cap.
- **Portfolio-level stop-outs or heat limits.** A cap on count, not on aggregate open risk.

## Further Notes

One consequence deserves stating because it removes a tool used repeatedly this week. While
each coin is simulated independently, any subset's metrics can be recomputed exactly by
filtering the trade log — which is how 2,000 coin subsets and four walk-forward splits were
tested in minutes rather than hours. **A concurrent cap destroys that property.** Once whether
a trade exists depends on what else was open, the log can no longer be filtered to answer a
different question; every variant needs a full replay. The measurement in this feature is
therefore genuinely expensive, and that cost is the price of modelling the portfolio honestly.

The expected result is a trade-off, not a free win. Capping declines winners along with
correlated losers, so expectancy should fall while drawdown falls faster. If drawdown halves
for a tenth of the expectancy, that is a good trade for an account that cannot survive a 75%
decline. If expectancy collapses, the cap is not the answer and this file will say so — as it
already does for coin selection, faster timeframes and the RSI plateau.

---

## Technical Annex
> Written against codebase as of: 2026-08-19

### Architectural Decisions

**The central change: the backtest becomes chronological.** Today `Backtester.run` iterates
`config.watchlist`, calls `run_coin` for each, and extends a flat outcome list — each coin
walked start to finish in isolation, with no shared state. A concurrent cap cannot be bolted
onto that shape, because at the moment a trade would open there is nothing that knows what
else is open.

The replay is restructured to advance over a merged timeline of decision points. At each
point: evaluate every coin whose reference bar closes then (reusing the existing per-moment
evaluation unchanged), collect the qualifying setups, ask the portfolio which may open, and
simulate the admitted ones. This keeps the existing evaluation and simulation code intact —
only the loop order changes.

**Equivalence is a hard requirement, and testable.** With no cap the chronological replay must
produce the same outcomes as the per-coin replay. That is what makes the restructuring safe to
land: a test runs both over the shared fixture and asserts identical trade logs. The existing
per-coin path is retained for exactly this comparison rather than deleted.

**A pure portfolio value, not mutable state.**

```python
@dataclass(frozen=True)
class PortfolioState:
    open_by_direction: Mapping[Direction, int]

    @property
    def total_open(self) -> int: ...

def admit(
    state: PortfolioState,
    candidates: Sequence[Candidate],      # qualifying setups at one moment, unordered
    *,
    max_total: int | None,
    max_per_direction: int | None,
) -> tuple[tuple[Candidate, ...], tuple[Declined, ...]]:
```

Pure in, pure out — the slot arithmetic is asserted directly without running a backtest, and
the declined list carries a reason (`total_cap` / `direction_cap`) so the report can explain
itself rather than silently omitting setups.

**The ranking rule is a decision, not an accident.** When more setups qualify than there are
slots, candidates are ordered by engine total descending, ties broken by symbol — identical to
the AI analyst's existing `candidate_gate.select`, and deliberately so: two different orderings
for "which setups matter most" would be a bug waiting to happen. Reusing the same rule also
means `reserve_per_direction` thinking is available later if measurement shows the cap should
balance directions rather than merely limit them.

**Configuration.** A new block, both limits unset (unlimited) by default:

```yaml
portfolio:
  max_open: null            # total concurrent positions; null = unlimited (today's behaviour)
  max_open_per_direction: null   # per-direction limit; null = unlimited
```

`null` rather than a large number, so "unlimited" is explicit in the code path and the
no-cap case provably runs today's logic. Zero or negative is a `ConfigError` naming the field.

**Reporting.** `BacktestReport` gains `declined_by_cap: int`, `concurrency_histogram:
Mapping[int, int]` (how many decision points had N positions open), and
`declined_winners: int` — the last computed by simulating declined setups *counterfactually*
so the cost of protection is stated, not hidden. All three are omitted from the rendered
report when no cap is set, keeping the uncapped output byte-identical.

**Live path is advisory.** `run_scan` renders how many surfaced setups fit the configured cap
and which would be declined, and `SetupBrief`'s market context gains the free-slot count so
the analyst can weigh a marginal setup against the last slot. No position tracking is added;
the count assumes a flat book, and the rendered text says so.

### Automated Testing Decisions

A good test here asserts slot arithmetic or a trade log, never internal loop mechanics. The
prior art is `tests/baseline_fixture.py` (a deterministic multi-trade history) and the existing
backtester tests: hand-built frames, no network, no wall clock.

- **`admit`** — unit, the densest module. Five qualifying setups against three slots admits three; a full book admits none; a freed slot admits the next; the per-direction limit blocks a third short while allowing a long; the total limit binds before the direction limit when tighter; ties break deterministically; declined entries carry the correct reason; an unlimited cap admits everything; a cap of one admits one.
- **`PortfolioState`** — unit. Slot accounting on open and close, including closing a direction that is not at its limit, and `total_open` agreeing with the per-direction sum.
- **Chronological replay equivalence** — integration, the load-bearing test. Both replays over `baseline_fixture` with no cap produce identical trade logs (symbol, direction, entry and exit time, R-multiple), asserted element-wise.
- **Cap behaviour in the replay** — integration over a purpose-built history where many coins qualify simultaneously: a cap of one yields no overlapping trades in the log; a cap of three never exceeds three concurrent; the declined count is non-zero and matches what `admit` would have declined.
- **Reporting** — unit. The histogram sums to the number of decision points; `declined_winners` counts only declined setups whose counterfactual outcome was a win; with no cap all three fields are absent from the rendered output.
- **`config.py`** — unit, extending the existing pattern. Both fields default to `None`; unknown keys inside `portfolio:` are rejected by name; zero and negative are rejected naming the field.
- **`cli.py`** — integration with injected fakes: the scan states how many setups fit and which are declined; the analyst's evidence carries the free-slot count; with no cap the output matches the pre-existing scan tests unmodified; the momentum scanner's output is unchanged with a cap configured.
- **`tests/test_core_independence.py`** — extended to cover the new portfolio module by name.

No test may perform network I/O, read a real credential, or write outside `tmp_path`.
