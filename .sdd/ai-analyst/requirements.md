# AI Analyst

## Problem Statement

The trader currently has to sit at a desk to get value out of the bot. The local engine
scans the 87-coin watchlist and prints a ranked table of setups to a terminal, but
turning that table into a trading decision still requires a human to open charts, read
the multi-timeframe context, weigh the numbers against each other, and decide whether a
surfaced setup is actually worth taking. That judgement step is the slow part, it happens
at a screen, and it does not happen at all when the trader is away.

Three consequences follow:

- Setups surface while nobody is watching, and are seen hours later or not at all.
- Every surfaced setup gets the same treatment, because the engine's score is a number
  and not an opinion — it cannot say "this one is technically valid but the context is
  poor, wait".
- There is no record of *why* a setup was taken or skipped, so the quality of the
  judgement layer can never be measured or improved.

The engine's own measured edge is thin (a full backtest over 2022-2026 produced +0.065R
per trade, profit factor 1.10, and a 75% maximum drawdown, largely from clusters of
correlated short positions losing together on market-wide rallies). Any judgement layer
added on top must therefore be measurable from day one, so it can be shown to help or be
removed — it cannot be assumed to help.

## Solution

Add an AI analyst stage to the existing pipeline. After the local engine has done its
cheap, deterministic work and produced its short list of surfaced setups, a small,
cost-controlled selection of those candidates is packaged into a compact evidence brief
and sent to a large language model, which returns a per-candidate verdict: **LONG**,
**SHORT**, **WAIT**, or **AVOID**, with a confidence level, concrete trade levels, a
short rationale, and the key risks it sees.

Because all candidates from one scan are reviewed together in a single request, the
analyst can also see the shape of the whole basket and warn when the setups are
concentrated in one direction on correlated assets — the exact failure mode behind the
engine's historical drawdown.

Verdicts that clear a confidence threshold are pushed to the trader's phone via a
Telegram bot, so the trader learns about a high-quality setup wherever they are rather
than only when sitting at the terminal. Every verdict — including the ones that are not
pushed — is appended to a decision log on disk, together with the engine's own score and
trade plan at that moment, so the analyst's contribution can later be measured against
the engine acting alone.

The stage is off by default, cost-bounded by configuration, and strictly advisory: it
places no orders, and every alert says so.

The local engine remains the first filter. Thousands of polled market updates produce
zero language-model calls until the engine surfaces something worth an opinion.

## User Stories

1. As a trader, I want the bot to form an opinion on each surfaced setup, so that I do not have to open charts and analyse them myself.
2. As a trader, I want each opinion expressed as one of LONG, SHORT, WAIT or AVOID, so that I get an unambiguous instruction rather than a paragraph to interpret.
3. As a trader, I want a confidence level attached to every opinion, so that I can size my attention and my risk according to conviction.
4. As a trader, I want concrete entry, stop-loss and take-profit levels in the opinion, so that I can act on it without re-deriving the trade plan.
5. As a trader, I want a short written rationale with every opinion, so that I can sanity-check the reasoning instead of trusting a black box.
6. As a trader, I want the key risks and the invalidation condition stated explicitly, so that I know in advance what would make the setup wrong.
7. As a trader, I want to be told when the analyst disagrees with the local engine, so that conflicts between the two are visible rather than silently resolved.
8. As a trader, I want a warning when the surfaced setups are concentrated in one direction across correlated coins, so that I avoid the clustered-loss pattern that caused the engine's historical drawdown.
9. As a trader, I want high-confidence setups pushed to my phone, so that I learn about them while away from my desk.
10. As a trader, I want to set a minimum confidence for phone alerts, so that my phone is not buzzing for marginal setups.
11. As a trader, I want the phone alert to contain everything I need to decide — coin, direction, levels, confidence, rationale — so that I do not have to return to the terminal to act.
12. As a trader, I want every alert to state that it is advisory and that no order has been placed, so that there is never any doubt about whether the bot is trading my money.
13. As a trader, I want the terminal to keep showing the full results table exactly as it does today, so that adding the analyst does not take away the view I already rely on.
14. As a trader, I want the analyst's verdict shown alongside the engine's score in the terminal, so that I can compare them at a glance.
15. As a trader, I want every verdict written to a decision log on disk, so that I can measure later whether the analyst improved my results.
16. As a trader, I want the log entry to include the engine's score and trade plan at the moment of the verdict, so that I can compare "engine alone" against "engine plus analyst" on the same setups.
17. As a trader, I want the log to record which model produced each verdict and how many tokens it used, so that I can attribute both quality and cost.
18. As a trader, I want the log to be append-only and machine-readable, so that a later analysis tool can consume the whole history without a database.
19. As a trader, I want the analyst stage disabled by default, so that existing scans keep working unchanged and I never incur a surprise bill.
20. As a trader, I want to enable or disable the analyst from the command line for a single run, so that I can try it without editing configuration.
21. As a trader, I want a hard cap on how many candidates are sent to the model per run, so that a broad market move cannot produce an enormous bill.
22. As a trader, I want a minimum engine score below which nothing is sent to the model, so that I only pay for opinions on setups that already passed my own filter.
23. As a trader, I want the same coin and direction not to be re-reviewed on every poll while the market has not moved, so that a five-minute watch loop does not pay for the same opinion twelve times an hour.
24. As a trader, I want to see what would have been sent to the model without actually sending it, so that I can inspect and tune the evidence and the cost before spending anything.
25. As a trader, I want an estimated cost reported for each run, so that I know what the feature is costing me as I use it.
26. As a trader, I want my API credentials read from the environment and never from a configuration file, so that I cannot accidentally commit a secret to the repository.
27. As a trader, I want a clear error if the analyst is enabled but its credentials are missing, so that I am not left wondering why no opinions appeared.
28. As a trader, I want a failure of the language model to leave the rest of the scan intact, so that one outage does not cost me the analysis I already paid to compute.
29. As a trader, I want a failure to send a phone alert to be reported but not to abort the run, so that a messaging outage never loses me a scan.
30. As a trader, I want a malformed or nonsensical model response to be rejected rather than displayed, so that I am never shown a fabricated trade level.
31. As a trader, I want the model provider to be swappable, so that I can move between GPT, Claude or another vendor without rebuilding the feature.
32. As a trader, I want the momentum movers scanner to be reviewable by the analyst too, so that both of my scanners benefit from the same judgement layer.
33. As a trader, I want the evidence sent to the model to be compact and structured, so that cost stays low and the model is not distracted by irrelevant detail.
34. As a trader, I want the evidence to include the multi-timeframe picture the engine already computed, so that the analyst reasons from the same facts I would read off the charts.
35. As a trader, I want the evidence to include the current BTC regime, so that the analyst weighs market-wide context and not just the individual coin.
36. As a trader, I want coins flagged as having limited price history to be marked in the evidence, so that the analyst discounts its confidence accordingly.
37. As a trader, I want the analyst stage to work inside the existing repeating watch loop, so that I can leave the bot running all day and simply receive alerts.
38. As a trader, I want alerts to be de-duplicated within the watch loop, so that a single setup does not alert me repeatedly while it remains valid.
39. As a trader, I want the analyst never to place an order under any circumstance, so that the system remains advisory in this version.
40. As a trader, I want the language model's instructions to state the advisory-only constraint and the required output shape, so that its answers are consistent and safe.
41. As a trader, I want to be able to run with the analyst enabled but alerts disabled, so that I can build up a decision log for measurement before trusting the alerts.
42. As a trader, I want the decision log to record when a candidate was selected but the model failed, so that gaps in the record are explainable.
43. As a trader, I want the analyst's opinion to be able to override the engine's suggested levels, so that a better-reasoned entry or stop is not discarded.
44. As a trader, I want any level the analyst proposes to be sanity-checked for direction and geometry, so that an impossible trade plan is never shown to me.
45. As a maintainer, I want the analyst modules to be free of terminal-rendering dependencies, so that the analysis core remains usable from any interface, consistent with the rest of the codebase.

## User Acceptance Tests

1. Given the analyst is not enabled, when a scan is run, then the results table is identical to the current behaviour and no language-model request is made.
2. Given the analyst is enabled and the engine surfaces three setups, when a scan is run, then a single language-model request is made covering all three candidates.
3. Given the analyst is enabled and the engine surfaces nothing, when a scan is run, then no language-model request is made and no alert is sent.
4. Given a candidate is reviewed, when the verdict is returned, then the terminal shows the coin's action, confidence and rationale next to the engine's own score.
5. Given a verdict of LONG, when it is displayed, then it includes an entry price, a stop-loss, at least one take-profit level and a stated invalidation condition.
6. Given a verdict of WAIT or AVOID, when it is displayed, then a reason is shown and no alert is pushed for that candidate.
7. Given the analyst returns a verdict whose direction contradicts the engine's decided direction, when the results are displayed, then the disagreement is visibly marked.
8. Given the surfaced candidates are all in the same direction on correlated coins, when the analyst reviews them, then its response includes a concentration warning and that warning is shown to the trader.
9. Given a verdict's confidence is at or above the configured alert threshold, when the run completes, then a Telegram message is delivered to the configured chat.
10. Given a verdict's confidence is below the configured alert threshold, when the run completes, then no Telegram message is sent for that verdict.
11. Given a Telegram alert is delivered, when it is read on a phone, then it contains the coin, the action, the confidence, the entry, the stop-loss, the take-profit levels, a short rationale, and a statement that the message is advisory and no order was placed.
12. Given alerts are configured as disabled while the analyst is enabled, when a scan is run, then verdicts appear in the terminal and in the decision log but no message is sent.
13. Given the Telegram service is unreachable, when a scan is run, then the failure is reported to the trader and the scan still completes with its table, verdicts and log entries intact.
14. Given the analyst is enabled, when a scan produces verdicts, then one record per verdict is appended to the decision log file.
15. Given a decision log record is inspected, then it contains the timestamp, the coin, the engine's score, the engine's trade plan, the analyst's action, confidence, levels and rationale, the model name, and the token usage.
16. Given a decision log already contains records from previous runs, when a new run completes, then the previous records are still present and the new ones are appended after them.
17. Given a candidate is selected for review but the language model call fails after its retries, then a record is appended to the decision log marking that candidate as failed with the reason.
18. Given the language model is unreachable, when a scan is run, then the engine's own results table is still displayed in full and the run exits without error.
19. Given the language model returns a response that does not match the required output shape, when it is processed, then the response is rejected, no fabricated verdict is displayed, and the failure is recorded.
20. Given the analyst proposes a stop-loss above the entry for a long trade, when the verdict is validated, then the verdict is rejected as geometrically impossible and is not displayed as a tradeable plan.
21. Given a maximum of three candidates per run is configured and the engine surfaces eight setups, when a scan is run, then only the three highest-scoring setups are sent to the model.
22. Given a minimum review score is configured, when a setup surfaces below that score, then it is not sent to the model regardless of available capacity.
23. Given a coin and direction were reviewed in the previous poll and the reference timeframe has not produced a new candle, when the next poll runs, then that coin is not sent to the model again.
24. Given a coin was reviewed and the reference timeframe has since closed a new candle, when the next poll runs, then that coin is eligible for review again.
25. Given the dry-run option is used, when a scan is run, then the exact evidence that would be sent to the model is displayed, no request is made, and no cost is incurred.
26. Given a run has completed with the analyst enabled, then an estimated cost for that run is reported to the trader.
27. Given the analyst is enabled and the required credential environment variable is not set, when a scan is started, then the trader is told which variable is missing and the run stops before any market data is fetched.
28. Given credentials are supplied only through environment variables, when the configuration file is inspected, then it contains no secret values.
29. Given the analyst is enabled from the command line for a single run while disabled in configuration, when that run executes, then the analyst is active for it and inactive for subsequent runs.
30. Given the watch loop is running with the analyst enabled, when the same setup remains valid across several polls, then the trader receives at most one alert for it per new reference candle.
31. Given the momentum movers scanner is run with the analyst enabled, then its surfaced movers are reviewed and alerted using the same rules as the trend scanner.
32. Given a coin marked as having limited price history is reviewed, then the evidence sent to the model states that limitation.
33. Given any scan with the analyst enabled, then the current BTC regime is included in the evidence sent to the model.
34. Given any verdict produced by the system, then no order is placed on any exchange and no exchange trading credential is required to run the feature.

## Definition of Done

- All user acceptance tests pass.
- The analyst stage is disabled by default; a run with default configuration behaves exactly as it does today.
- Enabling the analyst requires no change to how market data is fetched or scored — the existing scan and movers results are unchanged by its presence.
- No secret value is read from, or required to be written into, a configuration file.
- A failure in the language model, the network, or the messaging service never prevents the engine's own results from being displayed, and never causes a non-zero exit.
- Every verdict, including rejected and failed ones, is represented in the decision log.
- The decision log format is documented and stable enough for a later measurement tool to consume.
- Cost controls (minimum score, maximum candidates per run, re-review cooldown) are all configurable and all enforced.
- No order-placement capability exists anywhere in the feature.
- Every trader-facing output of the feature carries the advisory-only statement.
- The analysis modules introduced by this feature do not depend on terminal-rendering libraries.
- All existing tests continue to pass, and the project's quality gates (tests, linting, type checking) are green.
- User-facing documentation describes how to enable the feature, which environment variables it needs, what it costs, and how to read the decision log.

## Out of Scope

- **Order execution of any kind.** This version is advisory only. No exchange trading keys, no position management, no automated entry or exit.
- **Replacing the local engine's analysis.** The analyst is a judgement layer on top of the existing scoring; it does not compute indicators, structure or direction itself.
- **WebSocket / streaming market data.** The polling architecture is retained deliberately for this version.
- **Backtesting the analyst.** This feature produces the decision log that makes measurement possible; the measurement tool itself, and any claim that the analyst improves results, is a separate future feature.
- **Chart images or vision-model input.** The evidence is numeric and textual only.
- **Two-way Telegram interaction.** Alerts are outbound only; commands sent back to the bot are not handled.
- **Multiple alert destinations.** A single Telegram chat, plus terminal output and the log file. Email, desktop notifications and webhooks are not included.
- **Provider implementations other than OpenAI.** The protocol is designed to be swappable and a null implementation exists for testing, but only one real provider is built here.
- **Position sizing and portfolio risk management.** The concentration warning is advisory text, not an enforced constraint.
- **Persisting analyst state across machines.** The decision log and cooldown state are local files.

## Further Notes

The measured performance of the underlying engine — +0.065R per trade, profit factor
1.10, 35.8% win rate, 75.1% maximum drawdown across 1,362 backtested trades — is the
reason the decision log is a first-class requirement rather than a nice-to-have. An
opinion layer that cannot be measured is indistinguishable from a more expensive random
filter. Nothing in this feature should be described as improving results until the log
has been analysed.

The cost model matters to the design. The engine polls continuously and may surface the
same setups repeatedly; without the score floor, the per-run cap, and the per-candle
cooldown, a watch loop would re-purchase the same opinion many times per hour. These
three controls are load-bearing, not optional polish.

The single-request-per-run design is a deliberate trade-off. It is cheaper than one
request per coin and it is the only arrangement in which the model can comment on the
composition of the whole basket, which is the specific weakness the backtest exposed. The
cost is that one malformed response affects all candidates in that run, which the
validation and graceful-degradation requirements address.

---

## Technical Annex
> Written against codebase as of: 2026-08-18

### Architectural Decisions

**Placement.** All new analysis modules live under `src/trader/` alongside the existing
ones and must not import `typer` or `rich`; `tests/test_core_independence.py` is extended
to cover them. Only `cli.py` renders. This mirrors the existing separation.

**New modules.**

| Module | Kind | Responsibility |
| --- | --- | --- |
| `brief.py` | pure | Builds the evidence pack sent to the model |
| `candidate_gate.py` | pure | Decides which setups are worth a model call |
| `analyst.py` | pure | Protocol, verdict types, validation, null implementation |
| `openai_analyst.py` | I/O adapter | The OpenAI-backed implementation |
| `decision_log.py` | I/O, thin | Append-only JSONL writer |
| `notify.py` | pure format + I/O send | Alert formatting and Telegram delivery |

**`brief.py` — the deep module.** Converts a `CoinAnalysis` (from `runner.py`) or a
`MomentumCoin` (from `movers.py`) into a `SetupBrief`: a frozen dataclass holding the
symbol, engine direction, engine total and per-category points, a per-timeframe summary
(EMA stack ordering, RSI, ATR as a percentage of price, relative volume, structure trend
state and the last swing high/low), the engine's `TradePlan` levels, liquidity (spread
and top-of-book depth), the `limited_history` marker, and the BTC regime as
`MarketBrief`. It emits plain JSON-ready values only — no pandas frames, no `Candles`
objects — so the payload is bounded and the module is trivially testable. A
`render_briefs(briefs, market) -> str` function produces the compact text block embedded
in the prompt; keeping rendering pure and separate from the API client is what makes
prompt content assertable in tests without a network.

**`candidate_gate.py`.** Pure selection over an `AnalysisRun`'s `setups` (or a
`MoversRun`'s surfaced movers):

```
select(candidates, *, min_score, max_candidates, cooldown: CooldownState,
       reference_bar_open_ms) -> tuple[Selected, ...]
```

Rules applied in order: drop below `min_score`; drop any `(symbol, direction)` whose last
review carries the same `reference_bar_open_ms` (the cooldown key — a new reference candle
re-opens eligibility); sort by engine total descending; take `max_candidates`.
`CooldownState` is an in-memory mapping owned by the caller so the watch loop carries it
across iterations; it is passed in rather than held as module state so the function stays
pure.

**`analyst.py`.**

```python
class Action(str, Enum):
    LONG = "long"; SHORT = "short"; WAIT = "wait"; AVOID = "avoid"

@dataclass(frozen=True)
class AnalystVerdict:
    symbol: str
    action: Action
    confidence: float            # 0-100
    entry: float | None
    stop_loss: float | None
    take_profits: tuple[float, ...]
    rationale: str
    key_risks: tuple[str, ...]
    invalidation: str | None
    agrees_with_engine: bool

@dataclass(frozen=True)
class AnalystReview:
    verdicts: tuple[AnalystVerdict, ...]
    concentration_warning: str | None
    model: str
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float

class Analyst(Protocol):
    def review(self, briefs: Sequence[SetupBrief], market: MarketBrief) -> AnalystReview: ...
```

`validate_verdict(verdict, brief) -> str | None` returns a rejection reason or `None`:
confidence within `[0, 100]`; for an actionable verdict, entry/stop/targets all present
and positive; for LONG, `stop_loss < entry < min(take_profits)`; mirrored for SHORT;
rationale non-empty. Rejected verdicts are downgraded to a recorded failure, never
displayed as tradeable. `NullAnalyst` returns an empty review and is the default when the
feature is off and the stand-in in tests.

**`openai_analyst.py`.** Depends on the `openai` SDK (new dependency, `openai>=1.40`). The
client is constructor-injected (`OpenAIAnalyst(client=..., model=..., ...)`) so tests use
a fake object exposing the same call surface and never touch the network — the same
injection pattern as `MarketDataProvider` and `AnalysisProvider`. The request uses
structured output (a JSON schema for the review object) so parsing is not regex work; a
schema-mismatch or transport error is retried with backoff up to a bounded count, after
which the whole review degrades to a recorded failure and the run continues. Temperature
defaults low. The system prompt states: advisory only, never instruct order placement,
answer strictly in the given schema, prefer WAIT under ambiguity, and explicitly comment
on directional concentration across the supplied candidates. The API key is read from the
environment variable named in configuration (default `OPENAI_API_KEY`) and is never
logged. Cost is estimated from the returned token usage against configured per-million
input/output rates.

**`decision_log.py`.** `DecisionLog(path, clock=time.time)` with
`append(record: DecisionRecord) -> None`, writing one JSON object per line, opening in
append mode and flushing per write, creating parent directories as needed. Records carry
`schema_version` so a later measurement tool can evolve. Both successful verdicts and
failures (`status: "ok" | "rejected" | "failed"`) are recorded, each with the engine's
score and plan snapshot. The clock is injected so tests assert timestamps.

**`notify.py`.** `Notifier` protocol with `send(text: str) -> None`. `format_alert(verdict,
brief, *, concentration_warning) -> str` is pure and returns the message text including
the advisory-only line. `TelegramNotifier(bot_token, chat_id, post=...)` performs one HTTP
POST to the Bot API `sendMessage` endpoint with the injected `post` callable defaulting to
a thin `urllib.request` wrapper — stdlib, so no HTTP dependency is added beyond what the
`openai` SDK already brings, and injectable so delivery is tested without a network.
Send failures raise a `NotifyError` that the CLI catches, reports and swallows.
`NullNotifier` is the default.

**Configuration (`config.py`).** Two new optional nested blocks, validated with the same
unknown-key rejection pattern as `_require_backtest`:

```yaml
ai:
  enabled: false
  provider: openai              # only value accepted in this version
  model: gpt-5                  # provider model id
  api_key_env: OPENAI_API_KEY   # variable name, never the key itself
  min_score: 80                 # engine total floor for review
  max_candidates: 5             # per run
  max_retries: 2
  temperature: 0.2
  input_cost_per_mtok: 0.0      # for the cost estimate
  output_cost_per_mtok: 0.0
  decision_log: .cache/decisions.jsonl

telegram:
  enabled: false
  bot_token_env: TELEGRAM_BOT_TOKEN
  chat_id_env: TELEGRAM_CHAT_ID
  min_confidence: 70
```

Both default to disabled, so an existing config file remains valid and an existing run
unchanged. New `AIConfig` and `TelegramConfig` frozen dataclasses mirror `BacktestConfig`.
Missing credentials while enabled raise `ConfigError` naming the variable, surfaced by the
CLI as exit code 2 before any market data is fetched.

**CLI wiring (`cli.py`).** `scan` and `movers` gain `--ai` / `--no-ai` (overriding
`ai.enabled` for one run) and `--dry-run-ai` (render and print the evidence, make no
request). The stage runs after the existing table render, inside `run_scan` and
`run_movers_cli`, in the order: gate → analyst → validate → log → render verdicts →
notify. `run_scan_forever` owns the `CooldownState` and passes the same instance into each
iteration, which is what makes cross-poll de-duplication work. `analysis_run_to_dict`
gains an optional `review` section so `--json` output stays a single source of truth with
the table. Every failure path in this stage is caught in the CLI and reported without
changing the exit code.

**Data flow.**

```
poll → run_analysis (unchanged) → setups
     → candidate_gate.select(min_score, max_candidates, cooldown)
     → brief.build_brief per candidate  → render_briefs
     → Analyst.review  (one request, all candidates)
     → validate_verdict per verdict
     → DecisionLog.append per verdict/failure
     → render verdict table
     → Notifier.send for verdicts >= min_confidence
```

**Dependencies added.** `openai>=1.40`. Telegram uses stdlib `urllib.request`. A `.env`
entry is added to `.gitignore` (currently only ignores `.cache/`).

### Automated Testing Decisions

A good test here asserts externally observable behaviour — the verdict returned, the
record written, the message text, the candidates selected — and never reaches into
private helpers or asserts on prompt wording beyond the facts that must be present.
Existing tests in `tests/test_movers.py`, `tests/test_runner.py` and
`tests/test_cli_scan_options.py` are the prior art: hand-built fixtures, injected fakes
for every I/O boundary, no network, no wall-clock sleeps.

Modules with automated tests:

- **`brief.py`** — unit. Given a hand-built `CoinAnalysis`, the brief contains the expected symbol, direction, per-timeframe fields, plan levels, liquidity and `limited_history` marker; contains no pandas objects; `render_briefs` output includes each symbol, the BTC regime, and the limited-history note when set. Same for `MomentumCoin`.
- **`candidate_gate.py`** — unit, the densest test module. Score floor excludes; cap takes the top-N by total; ordering is by engine total descending; a `(symbol, direction)` with an unchanged reference bar is excluded; the same pair with a new bar is included; an empty candidate list yields no selection; cooldown state is not mutated by the function.
- **`analyst.py`** — unit. `validate_verdict` accepts a well-formed long and short; rejects inverted stop/target geometry for each direction, out-of-range confidence, missing levels on an actionable verdict, and an empty rationale; `NullAnalyst` makes no calls and returns an empty review.
- **`openai_analyst.py`** — unit with a fake client. A well-formed structured response maps to the expected `AnalystReview` including token counts and estimated cost; a schema-mismatched response is retried and then degrades to a failure rather than raising; a transport error is retried up to the configured bound and then degrades; the request carries every candidate symbol and the advisory-only system instruction; the API key never appears in any log record.
- **`decision_log.py`** — unit against a `tmp_path`. One line per record, valid JSON, appended not overwritten across two writes; the injected clock's timestamp appears; failure and rejection records are written with the right status; parent directories are created.
- **`notify.py`** — unit. `format_alert` contains coin, action, confidence, all levels, rationale and the advisory-only statement; a WAIT verdict formats without trade levels; the concentration warning appears when present; `TelegramNotifier` calls the injected poster once with the configured chat id and the message body; a poster raising an error surfaces as `NotifyError`; `NullNotifier` sends nothing.
- **`config.py`** — unit, extending `tests/test_config.py`. The new blocks parse with defaults when absent; unknown keys inside `ai:` and `telegram:` are rejected; an enabled block with a missing credential environment variable raises `ConfigError` naming the variable; a config file containing a literal key value is rejected.
- **`cli.py`** — integration, extending the existing CLI test style with injected fakes. With the analyst disabled the fake analyst is never called and output matches today's; with it enabled the verdict column appears and one review call is made; `--dry-run-ai` prints the evidence and makes no call; an analyst that raises still yields a full results table and exit code 0; a notifier that raises is reported and exit code stays 0; a two-iteration watch loop with an unchanged reference bar makes exactly one review call.
- **`tests/test_core_independence.py`** — extended to assert the new analysis modules import neither `typer` nor `rich`.

No test may perform network I/O, read a real environment credential, or write outside
`tmp_path`.
