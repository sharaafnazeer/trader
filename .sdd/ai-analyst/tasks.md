# AI Analyst — Tasks

Derived from `.sdd/ai-analyst/requirements.md` (2026-08-18). Tasks are vertical slices in
topological order; the ordinal is the implementation order.

Project quality gates apply to every task: `uv run pytest`, `uv run ruff check`,
`uv run mypy src`, `uv run python -m compileall src`.

Ordering rationale: all three cost controls (score floor, per-run cap, per-candle
cooldown) land in tasks 01 and 02, before task 03 makes the first paid request. The
decision log lands before alerting, so no verdict is ever acted on before it can be
measured.

---

## Task 01-evidence-brief-and-dry-run

The trader can see exactly what the AI analyst *would* be shown, without spending
anything. A scan runs as it does today, and with the dry-run option it additionally
prints the compact evidence pack built from the setups it selected for review. This slice
cuts from YAML configuration through candidate selection and evidence construction to
terminal output, establishing the seams the later slices plug into. No model is contacted
and no model configuration exists yet.

Bounded selection belongs here rather than later: without a score floor and a per-run cap,
the dry run over an 87-coin watchlist would print an unbounded wall of evidence — the same
unbounded payload a real call would later pay for.

Note on the data join: a scan's surfaced setups are quality scores carrying only symbol,
direction, total and category breakdown. Everything the evidence needs — per-timeframe
features, structure, trade plan, order book, limited-history marker — lives on the
per-coin analysis records in the same run. Selection and evidence construction therefore
operate over the *paired* score and analysis for each coin, not over the setups alone.

### Implementation steps

- [x] Add an optional `ai:` configuration block, disabled by default, carrying only `enabled`, `min_score` and `max_candidates`, validated with the existing nested-block pattern including unknown-key rejection
- [x] Add the evidence types and a builder that turns a coin's paired quality score and analysis record into a JSON-ready brief: symbol, engine direction, engine total and per-category points, per-timeframe summary (moving-average stack ordering, RSI, ATR as a percentage of price, relative volume, structure trend state, last swing high/low), the engine's trade-plan levels, spread and top-of-book depth, and the limited-history marker
- [x] Add a market-context brief carrying the BTC regime decided once per run
- [x] Add a pure renderer turning a set of briefs plus the market context into the compact text block a model would receive
- [x] Add pure candidate selection over paired score-and-analysis records: apply the score floor, order by engine total descending, take at most the configured cap
- [x] Wire a dry-run option into the scan command that prints the rendered evidence after the existing results table
- [x] Document the new configuration block in the example configuration file
- [x] Append the new modules to the enumerated module list in the core-independence test

### Acceptance criteria

- [x] Running a scan with the dry-run option prints an evidence block containing, for every selected candidate, its symbol, engine direction, engine total, per-timeframe summary, trade-plan levels and liquidity figures, plus the BTC regime stated once for the run
- [x] Given eight surfaced setups and a cap of three, exactly the three highest-scoring setups appear in the evidence, in descending score order
- [x] A surfaced setup below the configured score floor never appears in the evidence, even when the cap leaves capacity
- [x] A candidate whose analysis carries the limited-history marker has that limitation stated in its evidence
- [x] The constructed brief holds only JSON-serializable values — no candle frames or other non-scalar objects — verified by serializing it in a test
- [x] A configuration file with an unknown key inside the new block is rejected with an error naming that key
- [x] A configuration file placing a literal credential value inside the new block is rejected with an error naming that key

### Quality gates

- [x] All four project gates pass
- [x] The new evidence and selection modules import neither `typer` nor `rich`, and are added to the enumerated list in the core-independence test rather than passing it by omission
- [x] No test in this slice performs network I/O, sleeps on the wall clock, or writes outside a temporary directory
- [x] The pre-existing scan and configuration test modules pass with no edits to their assertions

---

## Task 02-cooldown-across-the-watch-loop

The trader can leave the bot polling all day without the analyst re-selecting the same
setup on every pass. Candidate selection gains a cooldown keyed on the coin, the direction
and the reference timeframe's current candle, and the repeating watch loop carries that
state across iterations. Because this slice still runs in dry-run mode, the whole
cost-control mechanism is proven observable and correct before a single paid request is
ever made in the next slice.

The reference candle's open time is not exposed today: the candle wrapper offers only the
latest close, while the open time sits in the underlying frame's timestamp column. This
slice adds that accessor and reads the key from the reference timeframe's candles on the
coin's analysis record — the surfaced setups themselves carry no candles.

### Implementation steps

- [x] Add an accessor on the candle wrapper exposing the latest candle's open time, alongside the existing latest-close accessor
- [x] Add cooldown state keyed on coin, direction and reference-candle open time, owned by the caller and passed into selection so selection stays pure
- [x] Read the key from the configured reference timeframe's candles on the coin's analysis record
- [x] Extend candidate selection to drop any candidate whose key matches a previous review, applied before the score ordering and cap
- [x] Make the repeating watch loop own one cooldown instance and thread it through every iteration
- [x] Record each selected candidate's key into the cooldown state after selection
- [x] Document the cooldown behaviour and its interaction with the poll interval in the README

### Acceptance criteria

- [x] Across two consecutive polls in which the reference timeframe has not closed a new candle, a given candidate appears in the printed evidence on the first poll and not on the second
- [x] A poll in which the reference timeframe has closed a new candle for a previously selected coin selects that coin again
- [x] A coin previously selected in one direction is immediately eligible when the engine decides the opposite direction, without waiting for a new candle
- [x] Candidate selection does not mutate the cooldown state passed to it, verified by comparing the state before and after the call
- [x] Changing the configured reference timeframe changes which timeframe's candle the cooldown keys on, verified with two differently-configured runs over the same data
- [x] With the feature disabled, the watch loop selects no candidates and prints no evidence

### Quality gates

- [x] All four project gates pass
- [x] Watch-loop tests bound their iteration count and use an injected sleep — no wall-clock delay
- [x] The new candle accessor is purely additive: the pre-existing market-data test module passes with no edits to its assertions

---

## Task 03-openai-verdicts-in-terminal

The trader gets real opinions. The candidates selected by the previous two slices are sent
to OpenAI in a single request per run, and the returned verdicts — action, confidence,
levels, invalidation, rationale, risks — are rendered beside the engine's own score,
together with the run's token usage and estimated cost. Verdicts that are internally
inconsistent are rejected rather than shown, and any failure of the model or the network
leaves the engine's results fully intact.

The single-request-per-run shape is load-bearing: it is what lets the model comment on the
composition of the whole basket, so the directional-concentration warning belongs to this
slice rather than a later one.

Credentials come from the shell environment only — no environment-file loading is provided
— and the credential check must run *after* the command-line override is applied, so
enabling the analyst for a single run against a config where it is disabled still fails
loudly on a missing key rather than silently skipping validation.

### Implementation steps

- [x] Add the verdict vocabulary: the four actions; a per-candidate verdict carrying confidence, entry, stop-loss, take-profit levels, invalidation, rationale, key risks and an engine-agreement flag; and a review wrapping the verdicts with the concentration warning, model name, token counts and estimated cost
- [x] Add the analyst protocol and a null implementation used whenever the feature is off
- [x] Add pure verdict validation: confidence within range; an actionable verdict must carry positive entry, stop, at least one target, a stated invalidation and a non-empty rationale; a long requires stop below entry below its nearest target, and a short the mirror
- [x] Add the OpenAI-backed implementation with a constructor-injected client, a structured-output request schema, bounded retry with backoff, and cost estimated from returned token usage against configured rates
- [x] Write the system instruction stating advisory-only operation, the required output shape, a preference for WAIT under ambiguity, and an explicit instruction to comment on directional concentration across the supplied candidates
- [x] Extend configuration with the provider name (accepting only the OpenAI value in this version), model, credential environment variable name, retry bound, temperature and per-million token cost rates
- [x] Perform the credential presence check in the command layer after the command-line override resolves, exiting with a non-zero code and naming the missing variable before any market data is fetched
- [x] Thread an injected analyst through the scan entry points and the watch loop so tests can substitute a fake
- [x] Add the `openai` dependency
- [x] Add command-line options to enable or disable the analyst for a single run, overriding configuration
- [x] Render the verdicts, disagreement markers, the concentration warning, and the run's token usage and estimated cost after the results table
- [x] Add a review section to the structured JSON scan output carrying the same verdicts the table displayed
- [x] Document enabling the feature, the required environment variable, the provider restriction and the expected cost in the README

### Acceptance criteria

- [x] With three candidates selected, exactly one review request is made and it carries all three symbols; with no candidates selected, no request is made; with the feature disabled in configuration, a single scan never calls the analyst
- [x] Each verdict is displayed with its action, confidence, entry, stop-loss, take-profit levels, invalidation and rationale; a WAIT or AVOID verdict is displayed with its reason and without trade levels; where a verdict's levels differ from the engine's plan, the verdict's levels are the ones displayed
- [x] A verdict whose action contradicts the engine's decided direction for that coin is visibly marked as a disagreement, and a returned concentration warning is displayed
- [x] A verdict failing validation — inverted stop/target geometry for its direction, out-of-range confidence, missing levels or invalidation on an actionable verdict, or empty rationale — is rejected, is never displayed as a tradeable plan, and its rejection reason is reported
- [x] A transport error or a response not matching the required shape is retried up to the configured bound and then degrades: the full results table is still rendered and the command exits zero
- [x] Enabling the analyst from the command line against a configuration where it is disabled activates it for that run only; with the named credential variable unset, the run exits non-zero naming that variable before any market data is fetched; a configured provider other than the supported one is rejected with an error naming it
- [x] The run reports prompt and completion token counts and an estimated cost, and the structured JSON output's review section matches the verdicts rendered in the table

### Quality gates

- [x] All four project gates pass
- [x] The analyst modules import neither `typer` nor `rich`, and are added to the enumerated list in the core-independence test
- [x] The OpenAI implementation is exercised only through an injected fake client; no test performs network I/O or reads a real credential from the environment
- [x] The new modules import no exchange client and read no environment variable other than the configured credential variable, verified by a static check over their imports and environment access
- [x] The credential value appears in no rendered output and no log record, verified by asserting its absence in captured output for a run using a sentinel key value

---

## Task 04-decision-log-for-measurement

Every opinion the analyst forms is written to disk so its contribution can later be
measured against the engine acting alone. Each record pairs the verdict with the engine's
own score and trade plan at that moment, and records the model and token usage behind it.
Rejected and failed reviews are recorded too, so gaps in the history are explainable rather
than silent.

This is the slice that makes the feature falsifiable. Without it the analyst is an
unmeasurable filter with a per-token price — which, given the engine's measured profit
factor of 1.10, is the whole question.

### Implementation steps

- [x] Add an append-only line-delimited JSON writer with an injected clock and configurable path, creating parent directories as needed and flushing per write
- [x] Define the record shape with an explicit schema version, an identifier for the scanner that produced the candidate, the engine's total and trade-plan levels, the analyst's action, confidence, levels, invalidation, rationale and risks, the model name, and the prompt and completion token counts
- [x] Give each record a status distinguishing an accepted verdict, a verdict rejected by validation, and a review that failed outright, each non-accepted status carrying its reason
- [x] Add the log path to configuration with a documented default
- [x] Thread an injected log writer through the scan entry points so tests can substitute a temporary path
- [x] Write one record per selected candidate on every run with the analyst enabled, including candidates whose review failed
- [x] Document the record schema in the README as a fenced example record
- [x] Append the new module to the enumerated list in the core-independence test

### Acceptance criteria

- [x] A run producing three verdicts appends exactly three lines to the log, each parsing as a single valid JSON object
- [x] A record contains the timestamp, scanner identifier, coin, engine total, engine trade-plan levels, analyst action, confidence, analyst levels, invalidation, rationale, model name, prompt and completion token counts, and schema version
- [x] A verdict rejected by validation is recorded with the rejected status and its reason, and a review that failed after its retries produces one failed-status record per selected candidate carrying the reason
- [x] Running twice against the same log file leaves the first run's records present and unmodified, with the second run's records appended after them
- [x] The log file's parent directory is created when it does not exist, and the timestamp written is the one supplied by the injected clock
- [x] The key set of the fenced example record in the README equals the key set the writer produces, asserted in a test that parses both

### Quality gates

- [x] All four project gates pass
- [x] All decision-log tests write only inside a temporary directory
- [x] No credential value and no raw prompt text appears in any record, asserted against a record produced from a sentinel-keyed run
- [x] The log module imports neither `typer` nor `rich`, and is added to the enumerated list in the core-independence test

---

## Task 05-telegram-alerts-to-the-phone

The trader stops needing the desk. An actionable verdict whose confidence clears the
configured threshold is pushed to a Telegram chat containing everything needed to act —
coin, action, confidence, entry, stop-loss, targets, rationale — plus an explicit statement
that the message is advisory and no order has been placed. Delivery failures are reported
but never cost the trader a scan, and within a watch loop a setup alerts at most once per
reference candle, reusing the cooldown from task 02.

Confidence alone does not authorise an alert: a high-confidence WAIT is still a WAIT, and
pushing it to a phone would invert its meaning.

### Implementation steps

- [x] Add an optional `telegram:` configuration block, disabled by default, carrying the enabled flag, the bot-token and chat-id environment variable names, and a minimum confidence for alerting, validated with unknown-key rejection
- [x] Check both named environment variables in the command layer after any override resolves, exiting non-zero and naming the missing variable when alerting is enabled
- [x] Add a pure alert formatter producing the message text, including the advisory-only statement and, when present, the concentration warning
- [x] Add a notifier protocol, a null implementation used by default, and a Telegram implementation performing a single send through an injected sender
- [x] Surface a delivery failure as a dedicated error that the command layer reports and swallows
- [x] Send one alert per accepted LONG or SHORT verdict at or above the confidence threshold, reusing the per-candle cooldown so a persistent setup does not alert repeatedly
- [x] Document bot creation, the two environment variables, and the confidence threshold in the README
- [x] Append the new module to the enumerated list in the core-independence test

### Acceptance criteria

- [x] An accepted LONG or SHORT verdict at or above the configured threshold sends exactly one message to the configured chat; a verdict below the threshold sends none; a WAIT or AVOID verdict sends none regardless of its confidence
- [x] The message contains the coin, action, confidence, entry, stop-loss, take-profit levels and rationale, plus a statement that it is advisory and that no order was placed; a returned concentration warning is included in the run's alerts
- [x] With alerting disabled while the analyst is enabled, verdicts appear in the terminal and in the decision log and no message is sent
- [x] A sender that raises causes the failure to be reported to the trader while the run completes with its table, verdicts and log records intact and a zero exit code
- [x] Across a watch loop in which a setup remains valid over several polls with no new reference candle, at most one alert is sent for it
- [x] Enabling alerting while either named environment variable is unset exits non-zero naming the missing variable

### Quality gates

- [x] All four project gates pass
- [x] The notification module imports neither `typer` nor `rich`, is added to the enumerated list in the core-independence test, and introduces no HTTP dependency beyond the standard library
- [x] No test performs network I/O; delivery is exercised only through the injected sender
- [x] The bot token appears in no rendered output and no log record, asserted with a sentinel token value

---

## Task 06-analyst-for-the-momentum-scanner

The trader's second scanner gets the same judgement layer. The momentum movers command
gains evidence, review, logging and alerting, with evidence reflecting what momentum
actually measures — relative strength against BTC, breakout position, volume and
acceleration — rather than the trend engine's categories. Records from the two scanners
are distinguishable in the decision log so their contributions can be measured separately.

Two differences from the trend path are structural, not incidental. The movers run's
ranked entries carry only score and factors; the trade plan, order book and candles needed
for evidence live on its per-coin records, which must be filtered to the surfaced ones.
And the movers path fetches only the daily timeframe and has no repeating watch loop, so
its cooldown keys on the daily candle — at most one review per coin and direction per day.

### Implementation steps

- [x] Extend the evidence builder to construct a brief from a surfaced mover taken from the run's per-coin records: its composite momentum score and its relative-strength, breakout, volume and acceleration components, the trailing breakout level, the trade-plan levels, the order-book liquidity figures and the market context
- [x] Reuse the existing selection, review, validation, logging and alerting on the movers path, applying the configured score floor against the momentum score
- [x] Key the movers cooldown on the daily candle's open time
- [x] Add the analyst and dry-run options to the movers command, and thread the injected analyst, log writer and notifier through its entry point
- [x] Set the movers scanner identifier on every record and name the source scanner in every alert from this path
- [x] Render verdicts in the movers output alongside the existing momentum table and disclaimer
- [x] Document analyst support for the movers command in the README

### Acceptance criteria

- [x] Running the movers command with the analyst enabled reviews only surfaced movers, under the configured score floor and per-run cap; a non-surfaced mover is never reviewed
- [x] A mover's evidence contains its composite momentum score, its relative-strength, breakout, volume and acceleration components, its trailing breakout level, its trade-plan levels and its liquidity figures
- [x] Running the movers command with the dry-run option prints the evidence and makes no request
- [x] Two movers runs on the same daily candle review a given coin and direction once; a run after a new daily candle reviews it again
- [x] Decision-log records written from the movers path carry the movers scanner identifier and are distinguishable from trend-scanner records in the same file
- [x] An alert originating from the movers path names that scanner as its source
- [x] The existing momentum table and its advisory disclaimer are unchanged, with the pre-existing movers test modules passing with no edits to their assertions

### Quality gates

- [x] All four project gates pass
- [x] Every module touched or added on this path imports neither `typer` nor `rich` and is covered by the enumerated list in the core-independence test
- [x] No test performs network I/O or writes outside a temporary directory
- [x] The README documents, for both scanners, how to enable the analyst, the required environment variables, the three cost controls, the decision-log schema and the alerting threshold
