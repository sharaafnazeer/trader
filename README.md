# trader — advisory crypto trading bot

An **advisory** crypto tool with **two independent scanners** over your watchlist:

- **`scan` — the trend engine.** Reads live market data (Binance candles + order book) and
  TradingView's technical rating, evaluates each coin against **named strategies from a
  specific method** (a seven-condition checklist per setup),
  decides a **direction** (long / short / none), and — for setups that clear a quality
  threshold — produces a concrete **trade plan** (entry, stop-loss, take-profit, R:R). It can
  also **backtest** those signals over history to measure their edge.
- **`movers` — the momentum engine.** A deliberately *opposite* selection criterion: it ranks
  coins by **what's actually moving** — relative strength vs BTC, breakouts, volume expansion,
  and acceleration — to surface the volatile movers the trend engine avoids and loses on. Live
  only for now (no momentum backtest yet), with a breakout/ATR trade plan per surfaced mover.

On top of either scanner, an optional **[AI analyst](#ai-analyst)** reviews the setups the
local engine surfaces and returns **LONG / SHORT / WAIT / AVOID** with levels and reasoning,
pushed to **Telegram** so you don't have to watch a terminal. Off by default; every verdict
is logged so its value can actually be measured rather than assumed.

> **It never places trades.** Every output is advisory. The signals are heuristic and their
> edge is **unproven** — the trend engine only after a large backtest, and the momentum engine
> not at all yet (its backtest is a deferred follow-up). Past performance ≠ future results.
> Adding a language model on top does not create an edge; it can only re-filter what the
> engine already found, which is why the decision log exists.

---

## Requirements

- **Python ≥ 3.11**
- **[uv](https://docs.astral.sh/uv/)** (manages the virtualenv and dependencies)
- Network access to Binance and TradingView (for live `scan` and for fetching history)

**No API keys are needed for the scanners themselves** — they use Binance public market data
and TradingView's public rating. Keys are only needed if you switch on the optional
[AI analyst](#ai-analyst) (an OpenAI key) and [Telegram alerts](#telegram-alerts--leaving-the-desk)
(a bot token). Both come from the environment or a gitignored `.env` — never from a config
file. See [Credentials](#credentials).

## Install

```bash
cd /Users/sharaafnazeer/Own/trader
uv sync
```

Run any command with `uv run trader <command> …`. (Or `source .venv/bin/activate` once, then
just `trader <command> …`.)

## Quick start

```bash
# Trend signals over the built-in default watchlist:
uv run trader scan

# Trend signals over your own watchlist, showing every coin + why:
uv run trader scan --config mywatch.yaml --all

# Momentum movers over your watchlist — what's actually moving, ranked:
uv run trader movers --config mywatch.yaml

# Measure the trend engine's historical edge (first run fetches + caches; later runs fast):
uv run trader backtest --config mywatch.yaml --from 2022-01-01 --to 2025-12-31
```

### Turning on the AI analyst

Work through these in order — the first two steps cost nothing and tell you what the third
will cost.

**1. See what would be sent, and how much of it there is.** Set `ai.enabled: true` in your
config, then:

```bash
uv run trader scan --config mywatch.yaml --dry-run-ai
```

This contacts no model. Count the candidate blocks it prints: that is how many coins clear
`strategies.review_within`, and it is the whole basis of your cost estimate. If it prints 20
blocks, lower `review_within` or `ai.max_candidates`.

**If it prints zero, that is a normal outcome, not a fault.** The checklist has seven
conditions and they are much tighter together than any of them alone; a scan where nothing is
ready is exactly what the method is supposed to produce on most days. `review_within: 2`
admits the near-misses — the coins one or two conditions short — which is where a model earns
its keep: it can see a condition about to complete, or a context that makes a ticked box
meaningless, and the mechanical checklist can do neither. Set it to `0` to review only
fully-ready setups.

> `ai.min_score` is the **momentum scanner's** floor, measured against its 0–100 composite.
> It does not apply to `scan`, which has no score to floor.

**2. Price it.** Look up your model's per-million-token prices and put them in the config as
`ai.input_cost_per_mtok` / `ai.output_cost_per_mtok`. Until you do, runs report *cost not
configured* rather than a misleading `$0.00`.

**3. One real run.** Get a key from [platform.openai.com](https://platform.openai.com/api-keys)
and put it in a `.env` in the project directory (gitignored) — or `export` it:

```bash
echo 'OPENAI_API_KEY=sk-...' >> .env && chmod 600 .env
uv run trader scan --config mywatch.yaml --ai
```

One request per run. The token count and estimated cost print at the bottom.

**4. Alerts to your phone.** Create a bot with [@BotFather](https://t.me/BotFather), send it a
message, then read your chat id from
`https://api.telegram.org/bot<TOKEN>/getUpdates`:

```bash
cat >> .env <<'EOF'
TELEGRAM_BOT_TOKEN=123456:AA...
TELEGRAM_CHAT_ID=-1001234567890
EOF
# in your config: telegram.enabled: true
uv run trader scan --config mywatch.yaml --ai --watch 300
```

Leave that running. The per-candle cooldown means 48 polls per 4h candle still cost one
request, so a short interval is free.

**5. Check the log before you trust it.**

```bash
wc -l .cache/decisions.jsonl                       # how many decisions so far
jq -r '.status' .cache/decisions.jsonl | sort | uniq -c    # accepted / rejected / failed
```

Every verdict is stored next to the engine's own checklist verdict. That pairing is what will eventually
answer whether the analyst helps — see [the decision log](#the-decision-log).

Prefer to build up evidence before acting on it? Set `ai.enabled: true` and leave
`telegram.enabled: false`. Verdicts go to the terminal and the log, and nothing buzzes.

---

## Commands

### `scan` — live signals right now
Scores each watchlist coin from live Binance + TradingView and prints the actionable setups.

```bash
uv run trader scan [--config FILE] [--all] [--details] [--json OUT] [--watch SECONDS] [--dry-run-ai]
```
- default view: the coins that are **ready to trade** — every checklist condition met — with
  trend, setup, entry status, decision and entry/SL/TP/R:R. When none is ready it says so.
- `--all` — show every analysed coin, closest to a complete checklist first, with the reason
  it is not ready (an unmet condition, or `lead_unresolved` / `btc_veto` / … when it never
  reached the checklist) and a limited-history marker.
- `--details` — add the reason and history columns to the default view.
- `--json OUT` — also write the full result as JSON.
- `--watch SECONDS` — re-run on a timer until Ctrl-C.
- `--ai` / `--no-ai` — enable or disable the AI analyst for this run, overriding
  `ai.enabled`. Enabling it makes one paid request per run. See [AI analyst](#ai-analyst).
- `--dry-run-ai` — print the evidence the AI analyst *would* be sent for this run's selected
  candidates. Contacts no model and spends nothing.

> `scan` fetches TradingView ratings **in one batched request per timeframe** (all watchlist
> symbols at once), so a large watchlist makes only a handful of TradingView calls and 429
> rate-limiting is rarely an issue. Large lists are split into chunks of `tradingview_batch_size`
> (default 100). If a batch still fails, that timeframe degrades gracefully (coins scored on
> Binance factors). With so few calls you can keep `tradingview_delay` low.

### `movers` — momentum scanner (what's moving)
A **second, independent** scanner. It scores each watchlist coin **0–100** on composite
momentum — **relative strength vs BTC** (40) + **breakout** (30) + **volume expansion** (20)
+ **acceleration** (10) — filters out illiquid coins, tags a direction (up-momentum → long,
down-momentum → short), and for coins clearing `momentum_threshold` attaches a **breakout/ATR
trade plan** (stop an ATR beyond the trailing breakout level, target floored by `target_rr`).

```bash
uv run trader movers [--config FILE] [--all] [--json OUT] [--ai|--no-ai] [--dry-run-ai]
```
- default view: the **surfaced short list** — liquid coins scoring ≥ `momentum_threshold`,
  ranked by score descending, with direction and entry/SL/TP/R:R.
- `--all` — show every evaluated coin, including below-threshold and liquidity-filtered ones,
  with a status column (`surfaced` / `below-threshold` / `illiquid`).
- `--json OUT` — write the full report (per-coin scores, factor breakdown, trade plans, the
  once-per-run BTC benchmark, and the advisory disclaimer) derived from the same result.
- `--ai` / `--no-ai`, `--dry-run-ai` — the same AI analyst as `scan`, with momentum-shaped
  evidence. See [AI analyst](#ai-analyst).

> It fetches the **BTC benchmark return once** per run, then live daily candles + one
> order-book snapshot per coin — no dependence on the historical cache. It reuses the trend
> engine's `min_depth` / `max_spread` liquidity filter and `atr_buffer` / `target_rr` / `long_only`
> settings, plus its own momentum keys (below). **Momentum entries are often late (near tops);
> its edge is unproven pending a backtest** — treat the output as a shortlist to investigate.

### `backtest` — measure the edge on history
Replays the exact live engine over historical candles (no look-ahead) and reports win rate,
expectancy, profit factor, max drawdown, and per-coin / per-direction breakdowns.

**The checklist gates the backtest exactly as it gates the scan.** A moment produces a trade
only when its strategy verdict reads READY — the same evaluator, on the same
reference-timeframe inputs. If it surfaced on a looser rule the numbers would describe a
strategy nobody runs. Set `strategies.enabled: false` to replay the direction rule alone.

```bash
uv run trader backtest [--config FILE] [--from DATE] [--to DATE] [--json OUT] [--csv OUT] [--demo]
```
- `--from` / `--to` — ISO dates (e.g. `2022-01-01`); only trades entered in the range count,
  earlier candles warm up the indicators. Defaults to the last 365 days.
- `--json OUT` — summary + full per-trade log; `--csv OUT` — the trade log.
- `--demo` — replay a built-in synthetic history (network-free smoke test).
- Without `--demo` it fetches real candles (paginated + cached; see **Caching**).

### Measuring a change — `--baseline`

Every tuning change should be answered with a number, not an argument. Save a report, flip
**one** setting, re-run with `--baseline`, and read the signed table:

```bash
# 1. Save the current behaviour.
uv run trader backtest --config bench-slow.yaml --from 2022-01-01 --to 2026-07-31 \
  --json baseline.json

# 2. Change exactly one thing, then compare.
uv run trader backtest --config bench-slow.yaml --from 2022-01-01 --to 2026-07-31 \
  --baseline baseline.json
```

```
          Change vs baseline (baseline.json)
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┓
┃ Metric          ┃ Baseline ┃ This run ┃ Change ┃ Verdict ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━┩
│ Resolved trades │     1362 │     1201 │   -161 │ —       │
│ Win rate %      │     35.8 │     38.4 │   +2.6 │ BETTER  │
│ Expectancy (R)  │    0.065 │    0.101 │ +0.036 │ BETTER  │
│ Profit factor   │     1.10 │     1.18 │  +0.08 │ BETTER  │
│ Max drawdown %  │     75.1 │     68.2 │   -6.9 │ BETTER  │
└─────────────────┴──────────┴──────────┴────────┴─────────┘
```

Trade count carries **no verdict** — more trades is neither good nor bad by itself. Drawdown
is the one metric where *down* is better.

**It refuses incomparable baselines.** A report records the window, watchlist size and cost
settings that produced it; if any of those differ the comparison is rejected with exit code 2
rather than presenting a difference caused by the setup as a finding. Missing or malformed
baseline files also exit 2.

**Budget the time.** A full-window run over the 88-coin watchlist takes roughly **3.5 hours**
(~0.0145s per point-in-time evaluation × 88 coins × ~9,855 4h closes; only the fetch is
parallel). For iterating, use a smaller config — `bench-slow.yaml` is a fixed 20-coin subset
that runs in about 50 minutes — and keep the same config for baseline and variant, or the
delta means nothing. Moving `reference_timeframe` to a faster rung multiplies the run time:
`bench-fast.yaml` (1h reference) is roughly 4× slower than the same config on 4h.

### `refresh-cache` — keep history warm (for cron)
Incrementally tops up the on-disk cache for the whole watchlist to the latest **completed**
candle, so later `backtest`/`scan` runs read warm data instead of re-downloading.

```bash
uv run trader refresh-cache [--config FILE] [--history-days N]
```
Intended to be scheduled once a day, e.g. crontab:
```
0 6 * * *  cd /Users/sharaafnazeer/Own/trader && uv run trader refresh-cache --config mywatch.yaml
```

### `market-data` — quick data check
```bash
uv run trader market-data --symbol BTC/USDT --timeframe 4h
```

### Logging / verbosity
Long background fetches and backtests can now report progress. Every command accepts:
- `-v` / `--verbose` — increase log verbosity: `-v` shows INFO (per-coin evaluation, fetch
  progress, cache-refresh summaries), `-vv` shows DEBUG (per-page fetch detail). With no flag
  only warnings are shown (retries/backoff, skipped coins, TradingView rate-limit degradation).
- `--log-file FILE` — also append the same logs to a file.

Logs go to **stderr**, so the stdout tables / `--json` / `--csv` output stays clean and pipeable:

```bash
uv run trader backtest -vv                       # watch fetch + backtest progress on stderr
uv run trader refresh-cache -v --log-file refresh.log
uv run trader scan --json out.json 2> scan.log   # JSON on stdout, logs on stderr
```

---

## Configuration

Pass a YAML file with `--config`. Every field is optional; omitted fields use defaults. A
ready example lives in `config.example.yaml`, and a personal 87-coin list in `mywatch.yaml`.

```yaml
profile: futures            # `futures` (15m/1h/4h/1d, daily leads, 4h levels) or
                            # `spot`   (1d/1w/1M, monthly leads, weekly levels)

watchlist:                  # Binance-quoted symbols, no slash (BTCUSDT, ETHUSDT, …)
  - BTCUSDT
  - ETHUSDT
exchange: BINANCE
screener: crypto

# Direction rule
lead_timeframe: 1d          # highest timeframe decides direction (overrides profile)
require_confirmation: false # false = lower HTF must merely NOT oppose the lead
btc_veto: true              # veto trades that fight BTC's regime
long_only: false            # true = suppress short setups

# Named strategies — the checklist (see "Strategies" below for what each knob means)
strategies:
  enabled: true
  max_extension_atr: 2.0        # ATR beyond the EMA21–EMA50 zone before the entry is a chase
  stoch_oversold: 0.20          # extreme a bullish momentum turn must come from (0–1 scale)
  stoch_overbought: 0.80        # …and a bearish one
  cross_lookback: 3             # bars ago a cross may be and still count as "just turned"
  pattern_wick_body_ratio: 2.0  # times its body a wick must be to count as a rejection
  reaction_lookback: 3          # recent bars the reaction candle may have formed on
  trendline_min_touches: 3      # pivots needed to call it a line (two always fit perfectly)
  trendline_min_r2: 0.7         # how well they must lie on it, 0–1
  trendline_tolerance_atr: 1.5  # how near price must be to have "returned to" the line
  review_within: 2              # conditions short of complete a coin may be and still be
                                # worth an AI opinion (0 = only fully-ready setups)
  evidence_candles: 30          # recent bars sent to the analyst with the evidence

# Liquidity / risk knobs
min_depth: 5000.0          # order-book depth floor as quote/USDT NOTIONAL (depth × price,
                           # top-20 levels) — used by `movers` to exclude untradeable coins
max_spread: 0.005
atr_buffer: 0.5            # ATR multiples padding the stop beyond invalidation
target_rr: 2.0            # minimum reward:risk the take-profit is floored to

# Momentum scanner (`movers`) — a second, independent engine
momentum_threshold: 60          # surface movers scoring at/above this (0–100)
momentum_weights:               # composite factor weights; defaults shown
  relative_strength: 40         # coin return minus BTC return over rs_lookback_days
  breakout: 30                  # ATR-normalized push past the trailing high/low
  volume: 20                    # relative volume vs its rolling average
  acceleration: 10              # rate-of-change of the move
rs_lookback_days: 30            # window for relative-strength-vs-BTC
breakout_lookback_days: 20      # trailing high/low window for the breakout level/stop

# Fetch behaviour
binance_delay: 0.25
tradingview_delay: 2.0          # can be low now that scan batches TradingView calls
tradingview_batch_size: 100     # symbols per batched TradingView request (scan)
ohlcv_lookback: 300
# watch_interval: 300       # default interval for `scan --watch`

# AI analyst (see below) — disabled by default; holds no credentials
ai:
  enabled: false
  min_score: 75             # momentum-scanner floor only; `scan` uses strategies.review_within
  max_candidates: 5         # most candidates reviewed per run
  provider: openai          # only `openai` is implemented
  model: gpt-5
  api_key_env: OPENAI_API_KEY   # env var name — never the key itself
  max_retries: 2
  # temperature: 0.2        # omitted by default; some reasoning models reject it
  input_cost_per_mtok: 0    # USD per 1M tokens, for the cost estimate only
  output_cost_per_mtok: 0
  decision_log: .cache/decisions.jsonl   # every verdict, for later measurement

# Telegram alerts (see below) — disabled by default; holds no credentials
telegram:
  enabled: false
  bot_token_env: TELEGRAM_BOT_TOKEN   # env var name — never the token itself
  chat_id_env: TELEGRAM_CHAT_ID
  min_confidence: 70        # analyst confidence needed to interrupt you

backtest:
  cache_dir: /Users/sharaafnazeer/Own/trader/.cache/backtest  # absolute = always here
  fee_rate: 0.0004          # per-side fee (net results; set 0 for gross edge)
  slippage: 0.0005
  risk_per_trade: 0.01      # fraction of equity risked per trade (equity curve)
  max_workers: 5            # concurrent history fetches
  history_horizon_days: 1825 # how far back the cache/backtest fills (1825 = 5y)
  # max_holding_bars: 96    # optional time-stop
  # start: 2022-01-01       # default range if --from/--to omitted
  # end:   2025-12-31
```

**Trading style presets.** `profile: futures` vs `spot` sets the timeframe set, the leading
timeframe, and the timeframe trade levels are drawn from. Override any of `timeframes`,
`htf_timeframes`, `lead_timeframe`, `reference_timeframe` explicitly if you want.

---

## Strategies

The engine recognises **named setups from a specific method**, not a generic score. Each is a
checklist, and the result is reported as four separate facts rather than one number:

```
Symbol    Trend   Setup   Entry            Decision   Reason
PULLUSDT  LONG    1A      READY (7/7)      ▲ LONG     every evaluated condition is met
ZECUSDT   LONG    1A      NOT_READY (5/7)  WAIT       extended 4.6 ATR above the 142.38-145.26
                                                      zone (limit 2.0) — wait for a pullback
```

- **Trend** — what the trend conditions found: LONG, SHORT, or NONE.
- **Setup** — which strategy matched: `1A`, `1B`, or `—` when neither applies.
- **Entry** — READY or NOT_READY, with how many conditions are met out of how many judged.
- **Decision** — LONG / SHORT when every entry condition is met, **WAIT** otherwise.
- **Reason** — composed from the unmet conditions, each naming a measured value.

**A trend existing is not an entry, and WAIT is the expected answer.** Most coins, most days,
are in a trend without being at an entry. A scan where nothing is ready says so in words; it
does not present the best of a bad list.

### Strategy 1A — long with the trend (pullback)

| # | Condition | Satisfied when |
| --- | --- | --- |
| 1 | Moving-average stack | `EMA10 > EMA21 > EMA50 > SMA200` on the decision timeframe |
| 2 | Market structure | higher highs **and** higher lows |
| 3 | Retracement zone | price inside the EMA21–EMA50 band, or no more than `max_extension_atr` beyond it *in the trade's direction* |
| 4 | Trendline | a rising line through the swing lows, with price within `trendline_tolerance_atr` of it |
| 5 | Reaction candle | a bullish engulfing, hammer/pin bar or morning star, **at the zone**, within `reaction_lookback` bars |
| 6 | Momentum turn | Stochastic RSI %K crossed **above** %D within `cross_lookback` bars, **from** at or below `stoch_oversold` |
| 7 | MACD | a bullish crossover within `cross_lookback` bars **and/or** the line above zero |

Conditions 1–2 decide the trend: fail either and the coin matches **no setup** rather than
waiting for one. Conditions 3–7 decide the entry: fail any and the decision is **WAIT**.

### Strategy 1B — short with the trend (retracement)

The exact mirror. `EMA10 < EMA21 < EMA50 < SMA200`; lower highs and lower lows; a **falling**
trendline; price rallied back into the zone, **rejecting from resistance** (not bouncing off
support); a bearish engulfing, shooting star or evening star; %K crossing **below** %D from at
or above `stoch_overbought`; MACD bearish by crossover and/or below zero.

### Reward-to-risk — a preference, not a gate

`target_rr: 2.0` floors every take-profit, so **1:2 is guaranteed** and a sub-2R plan cannot
reach you. What varies is whether that target is a level the market has respected or one the
planner placed to satisfy the arithmetic, and the Target column says which:

| Target | Meaning |
| --- | --- |
| `structural` | the next swing pivot reaches the ratio on its own |
| `manufactured` | no pivot in range; the target sits at exactly `target_rr` risk |

A structural target **ranks** a setup above an otherwise equal one. It never blocks: a coin at
new highs has no overhead pivot by definition, and gating on it would make breakouts
permanently untradeable.

### The closed indicator set

The engine computes **these and nothing else**:

> EMA10, EMA21, EMA50, SMA200, Stochastic RSI (%K/%D), MACD (line/signal/histogram), ATR,
> volume (raw and relative), and the raw candles. Derived from candles: swing structure,
> trendlines, candlestick patterns.

Indicators the engine once carried and no longer does — EMA20, EMA200, standalone RSI, rate of
change, on-balance volume, Bollinger band width — were removed rather than left unread, along
with the 0–100 quality score three of whose categories were built on them. A configuration
naming a retired setting is rejected by name with an explanation.

**Adding an indicator is a decision, not a convenience.** If a condition seems to need one
that is not on the list, that is a question to answer deliberately rather than a field to add.
A test enumerates the feature bundle against the list and fails on any name outside it.

### These strategies were measured, and they lose money

Replayed over 86 coins, 2022-01-01 → 2026-07-31, net of fees and slippage, against a control
that is the same engine with the checklist switched off:

| | Checklist ON | Control (OFF) |
| --- | ---: | ---: |
| Resolved trades | 1,121 | 5,226 |
| Win rate | 33.9% | 32.0% |
| Expectancy | **−0.0146R** | −0.3080R |
| Profit factor | **0.979** | 0.683 |
| Max drawdown | 61.9% | 100.4% |

**The checklist works. It just doesn't work enough.** It removes 79% of the trades the engine
would otherwise take and adds **+0.293R per trade** — without it the same engine is ruinous
(profit factor 0.68, a 100% drawdown, the account gone). With it, profit factor is 0.979:
still below 1.0, so **still not tradeable**.

The payoff is exactly the 2:1 specified (avg win +2.008R, avg loss −1.052R), which needs a
**34.4%** win rate to break even. It achieved **33.9%** — short by *half a percentage point*,
roughly five trades in eleven hundred. The risk model is sound; the hit rate is marginally too
low. See `.sdd/strategy-catalogue/results.md` for the full write-up and what was not measured.

No claim is made that this is profitable, because it is not.

## AI analyst

An optional stage that hands the setups the local engine surfaces to a language model for a
**LONG / SHORT / WAIT / AVOID** opinion with levels, an invalidation, a rationale and the key
risks it sees. **Disabled by default.** It works on **both scanners** — `scan` and `movers` —
with identical switches, cost controls, logging and alerting.

```bash
# Free: print exactly what the analyst would be sent, contact nothing.
uv run trader scan   --config mywatch.yaml --dry-run-ai
uv run trader movers --config mywatch.yaml --dry-run-ai

# Paid: one request per run, covering all selected candidates.
export OPENAI_API_KEY=sk-...
uv run trader scan   --config mywatch.yaml --ai
uv run trader movers --config mywatch.yaml --ai
```

`--ai` / `--no-ai` override `ai.enabled` for a single run. The credential check runs
**before any market data is fetched**, so a missing key costs you a second rather than a full
scan — including when you enable the analyst from the command line against a config that has
it switched off.

### Credentials

**Never in the config file.** Keys that would hold one verbatim (`api_key`, `token`,
`secret`, …) are rejected by name at load time. Two supported ways to supply them:

```bash
# 1. Export in your shell (or a profile / a file you `source`):
export OPENAI_API_KEY=sk-...

# 2. A .env file in the working directory (gitignored):
cat > .env <<'EOF'
OPENAI_API_KEY=sk-...
TELEGRAM_BOT_TOKEN=123456:AA...
TELEGRAM_CHAT_ID=-1001234567890
EOF
chmod 600 .env
```

**An exported variable always wins over `.env`.** The file fills gaps only, so a stale
`.env` can never silently shadow a key you deliberately exported — that failure mode costs
an hour of debugging the wrong thing. The run prints which *names* it loaded, never values.

`.env` is what makes a scheduled run work: `cron` and `launchd` start with no login shell,
so a key from `~/.zshrc` simply isn't there, while a file in the working directory is.
Point elsewhere with `--env-file /path/to/creds`, and note the file is read relative to the
**current directory**, not the config file's — so `cd` into the project first in any cron
line. Variable *names* are configurable per-block (`ai.api_key_env`,
`telegram.bot_token_env`, `telegram.chat_id_env`) if you want trader-specific keys.

Format: `KEY=VALUE` per line, `#` comments and blank lines skipped, a leading `export`
tolerated (so the same file also works with `source`), surrounding quotes stripped. Inline
comments are *not* stripped from unquoted values — a token can legitimately contain `#`,
and truncating one there would produce a baffling auth failure.

### The two scanners

Both go through the same review, validation, logging and alerting code. Only the *evidence*
differs, because the two engines measure different things and labelling them identically
would invite the model to conflate them:

| | `scan` (trend) | `movers` (momentum) |
| --- | --- | --- |
| Eligible candidates | coins within `strategies.review_within` conditions of a complete checklist | surfaced movers (score ≥ `momentum_threshold`, liquid) |
| Floor measured against | how many checklist conditions are met | the 0–100 composite momentum score (`ai.min_score`) |
| Breakdown in the evidence | the seven checklist conditions, each with its measurement | the four momentum factors (relative strength, breakout, volume, acceleration) |
| Timeframe rows | every configured timeframe (15m/1h/4h/1d) | one, daily — the only one this scanner fetches |
| Extra context | BTC regime | BTC benchmark return + the trailing breakout level |
| Structure | detected swing highs/lows | `not-measured` — this scanner anchors to the breakout level, so reporting a classification would present an absence as a finding |
| Cooldown keyed on | the `reference_timeframe` candle (`4h` by default) | the daily candle — at most one review per coin/direction per day |
| Log `scanner` field | `scan` | `movers` |

Both write to the same decision log, distinguished by that `scanner` field, so their
contributions can be measured separately from one file.

> `movers` has no `--watch` loop, so each invocation starts with a fresh cooldown. Within a
> single process the daily key still applies; across separate invocations on the same day it
> does not. If you schedule `movers`, run it **once a day**.

### What comes back

All candidates go in **one request per run**. That is cheaper than one request per coin, and
it is the only arrangement in which the model can see the whole basket — which is why it is
also asked for a **concentration warning** when the selected setups are the same directional
bet on assets that move together. That pattern is what produced the engine's 75% historical
drawdown, so it is worth a line of output.

Each verdict is shown beside the engine's own checklist reading — the setup it matched and
whether it judged the entry ready — with a `DISAGREES` marker naming what they differ on
(direction, entry, or setup) when the model
wants the opposite direction. Where the analyst's levels differ from the engine's mechanical
plan, the analyst's are the ones shown.

**Verdicts are validated before display.** A confident, well-written, geometrically impossible
trade — a long whose stop sits above its entry, a missing invalidation, a confidence of 900 —
is rejected and listed under *Rejected verdicts (not tradeable)* with the reason. It is never
rendered as something to act on.

**Failure never costs you the scan.** A model outage, a malformed response after its retries,
or a refusal is reported as a line of yellow text; the engine's own table, which you already
paid to compute, still prints and the command still exits 0.

### Cost controls

The local engine is the cheap first filter and is what keeps this affordable — thousands of
polled updates produce zero model calls until something surfaces. Three further controls bound
what any run can select:

| Control | Setting | Effect |
| --- | --- | --- |
| Checklist floor | `strategies.review_within` | A coin more than this many conditions short of a complete checklist is never reviewed. `ai.min_score` is the momentum scanner's equivalent. |
| Per-run cap | `ai.max_candidates` | At most N candidates per run, highest engine total first. A cooling candidate frees its slot for the next-best fresh one. |
| Direction reserve | `ai.reserve_per_direction` | Slots guaranteed to *each* direction before the rest fill by rank. Costs nothing extra — it redistributes the same budget. |
| Per-candle cooldown | *(automatic)* | A coin already reviewed **in the same direction on the same `reference_timeframe` candle** is not reviewed again until that timeframe opens a new one. |

**Two traps worth knowing, both measured on a real 87-coin pass:**

*(The three paragraphs below describe the retired 0–100 score and are kept because the
lesson generalises: any floor can sit above what the engine ever produces, and ranking by a
single number reviews only the crowded side. `strategies.review_within` and
`ai.reserve_per_direction` are the equivalents to watch now.)*

*The floor can sit above what the engine ever produces.* Top score on that pass was **79.2**,
so a floor of 80 reviewed nothing, ever — a silent no-op that looks exactly like a quiet
market. Check with `--dry-run-ai` before assuming.

*Ranking by score alone reviews only the crowded side.* That pass scored **44 shorts to 7
longs**, so the top 5 by score were 5 shorts and no long was ever looked at. And because the
engine's score measures *confirmed continuation*, a pullback long lands in the 50s by
construction — quiet volume and distance from the high are what make it a pullback. Those are
precisely the setups an analyst could add value on, and pure ranking guarantees it never sees
them. `reserve_per_direction` fixes the split, but it **cannot reach a candidate the floor
excludes, nor one below the cap's reach**: getting to the 4th-best long needed
`min_score: 50`, `max_candidates: 8`, `reserve_per_direction: 4` together.

The cooldown is what makes `--watch` affordable. Under the default futures profile the
reference timeframe is `4h`, so `--watch 300` polls 48 times per candle but selects a given
setup **once** — shortening the poll interval costs nothing extra. Only the reference
timeframe's candle re-opens eligibility; a new `15m` bar does not. A direction flip re-opens
it immediately, because a coin flipping from long to short is genuinely new information.

The cooldown lives in memory for the life of the `--watch` loop; it does not persist across
separate invocations.

Every run reports its prompt and completion token counts. Set `ai.input_cost_per_mtok` and
`ai.output_cost_per_mtok` to your model's prices to get a dollar estimate too — left at zero
the run says *cost not configured* rather than printing a misleading `$0.00`.

### The decision log

Every reviewed candidate is appended to `ai.decision_log` (default `.cache/decisions.jsonl`)
as one self-contained JSON object per line. **This is the point of the whole feature.** The
engine's measured edge is thin — profit factor 1.10, 35.8% win rate, 75% max drawdown over
1,362 backtested trades — so an opinion layer on top is indistinguishable from an expensive
random filter until you can compare *engine alone* against *engine + analyst* on the same
setups. Each record stores both halves at the moment of the decision, which is what makes
that comparison possible later.

`status` is one of:

| Status | Meaning |
| --- | --- |
| `accepted` | The verdict passed validation and was shown as tradeable. |
| `rejected` | A verdict came back but was unusable (`reason` says why); never shown as a plan. |
| `failed` | No verdict was obtained for this candidate (`reason` says why). |

Rejected and failed candidates are recorded too. A gap would quietly bias any later
measurement toward the runs that happened to work.

<!-- decision-record-example -->
```json
{
  "schema_version": 1,
  "timestamp": "2023-11-14T22:13:20+00:00",
  "scanner": "scan",
  "symbol": "SOLUSDT",
  "status": "accepted",
  "engine_direction": "LONG",
  "engine_total": 82.5,
  "engine_plan": {
    "entry": 363.0,
    "stop_loss": 353.3,
    "take_profit": 382.4,
    "risk_reward": 2.0,
    "invalidation": 355.0
  },
  "limited_history": false,
  "action": "long",
  "confidence": 74.0,
  "entry": 100.0,
  "stop_loss": 94.0,
  "take_profits": [112.0, 125.0],
  "invalidation": "4h close below 94",
  "rationale": "Trend intact with expanding volume.",
  "key_risks": ["BTC losing its range low"],
  "agrees_with_engine": true,
  "concentration_warning": null,
  "model": "gpt-5",
  "prompt_tokens": 2400,
  "completion_tokens": 350,
  "estimated_cost_usd": 0.0087,
  "reason": null
}
```

Field notes: `engine_*` is what the local engine decided; the unprefixed `action`, `entry`,
`stop_loss`, `take_profits`, `invalidation` are the analyst's own. `scanner` distinguishes
`scan` from `movers`. `agrees_with_engine` is computed here, not reported by the model — and
standing aside (`wait`/`avoid`) never counts as a directional disagreement. The prompt text
is deliberately **not** stored: records carry the model's answer, not the evidence sent.

A test asserts this example's field set matches what the writer actually produces, so it
cannot drift.

### Telegram alerts — leaving the desk

This is the part that means you don't have to watch a terminal. High-confidence
**LONG / SHORT** verdicts are pushed to a Telegram chat with everything needed to decide.

**Setup:**

1. Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`, follow the
   prompts, and copy the token it gives you.
2. Send your new bot a message, then get your chat id — the simplest way is to open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `result[0].message.chat.id`.
3. Export both and switch alerts on:

```bash
export TELEGRAM_BOT_TOKEN=123456:AA...
export TELEGRAM_CHAT_ID=-1001234567890
# in your config: telegram.enabled: true
uv run trader scan --config mywatch.yaml --ai --watch 300
```

Like the model credential, both are checked **before any market data is fetched**, and the
run stops naming whichever variable is missing.

**What gets sent — and what doesn't:**

| Verdict | Alerted? |
| --- | --- |
| `LONG` / `SHORT` at or above `telegram.min_confidence` | **Yes** — one message |
| `LONG` / `SHORT` below the threshold | No |
| `WAIT` / `AVOID`, any confidence | **Never** |
| Rejected by validation | **Never** |

The `WAIT`/`AVOID` rule is deliberate and is not the same as the confidence rule. The model
is asked to express real uncertainty, so it can legitimately return a *confident* WAIT — it
is confident the right move is to do nothing. Pushing that to a phone would invert its
meaning.

Each message carries the coin, action, confidence, entry, stop, targets, invalidation,
rationale, key risks, the concentration warning when present, the engine's own verdict
(marked `ANALYST DISAGREES` when the two conflict), and a line stating that nothing has
been traded.

Within a `--watch` loop, alerts inherit the per-candle cooldown: a setup that stays valid
across 48 polls of one 4h candle alerts **once**.

**A messaging outage never costs you a scan.** A failed send is reported as a yellow line
and the run continues — the table, the verdicts and the decision-log records all survive,
and one failing send does not swallow the others. Records are written *before* alerts are
sent, so the measurement history survives even when the phone never gets the message.

The bot token is redacted from every error message this code emits, because Telegram puts
it in the request URL and `urllib` quotes that URL back in exceptions.

## Caching

Historical candles are cached on disk under `backtest.cache_dir` as one CSV per
`SYMBOL__TIMEFRAME` (e.g. `BTCUSDT__4h.csv`). The cache is **incremental** (only fetches
candles newer than what's stored) and stores only **completed** candles (never the
still-forming one). Fetching is **concurrent**, bounded by `max_workers`.

- Use an **absolute** `cache_dir` so it always writes to the same place regardless of where
  you run the command.
- **Size:** 5 years of 15-minute candles is ~10 MB per coin — a large watchlist runs to
  hundreds of MB. Add `.cache/` to `.gitignore`.
- **The cache only extends forward.** It never back-fills candles older than what's already
  cached — so choose `history_horizon_days` (and/or a wide first `--from`) up front. To deepen
  an already-cached coin, delete its `SYMBOL__TIMEFRAME.csv` and refetch.
- Only Binance-tradeable symbols cache; others are reported as skipped.

---

## How the backtest works (in one paragraph)

It marches through history at each reference-timeframe close, slicing every timeframe to only
the candles closed by then (so there's no look-ahead), runs the *same* pipeline the live scan
uses, and opens one simulated trade per coin on a fresh qualifying setup. It resolves each
trade on the finer timeframe (target vs stop by first touch; a bar spanning both counts as a
stop), nets out fees/slippage, and excludes trades left open at the end. It then reports win
rate, expectancy, profit factor, and max drawdown. **Honesty limits:** historical order-book
liquidity isn't modeled (that scoring category gets neutral credit), trades are independent
(no shared capital), and a result is only meaningful over many trades.

---

## Important caveats

- **Advisory only** — no orders are placed; you act (or don't) yourself.
- **No proven edge** until you run a large backtest; even then it's historical, not a promise.
- **Crypto-tuned** — the model is designed for crypto. Non-crypto symbols (stocks, commodities)
  may fetch but their signals should not be trusted.
- **Rate limits** — `scan` fetches TradingView per timeframe in one batched call, so large
  watchlists rarely throttle. Note that the recommendation currently feeds **nothing**: it fed
  the retired 0–100 score, and the checklist does not use it. It is still fetched (~15s a
  scan) and carried, pending a decision to either surface it to the analyst or drop the stage.
- Nothing here is financial advice. Don't risk money you can't lose.

---

## Development

```bash
uv run pytest              # test suite
uv run ruff check          # lint
uv run mypy src            # type check (strict)
uv run python -m compileall src
```

The analysis core (`indicators`, `structure`, `trendline`, `patterns`, `strategy`,
`direction`, `trade_planner`, `momentum`, `movers`, `backtester`, `concurrent_loader`,
`cache_refresher`, …) is kept free of `typer`/`rich` so it can be reused by other interfaces;
`tests/test_core_independence.py` enforces this.

The checklist itself lives in three pure modules, each testable without a price series:

| Module | Responsibility |
| --- | --- |
| `indicators` | the closed set, plus crossings reported as *dated events* with the extreme they came from |
| `trendline` | least-squares fit through swing pivots, refusing a line the pivots do not support |
| `patterns` | one predicate per candlestick reaction, each asserted positively and negatively |
| `strategy` | the checklist itself — reads computed *facts*, never candles, so any row can be flipped in isolation |

The split is deliberate: `indicators` reports **what happened**, `strategy` decides **what
counts**. Thresholds live in configuration and are applied in `strategy`, which is why a
threshold change is a one-line test rather than a re-derivation.
