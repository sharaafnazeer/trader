# trader — advisory crypto trading bot

An **advisory** crypto tool with **two independent scanners** over your watchlist:

- **`scan` — the trend engine.** Reads live market data (Binance candles + order book) and
  TradingView's technical rating, scores each coin **0–100** across eight weighted categories,
  decides a **direction** (long / short / none), and — for setups that clear a quality
  threshold — produces a concrete **trade plan** (entry, stop-loss, take-profit, R:R). It can
  also **backtest** those signals over history to measure their edge.
- **`movers` — the momentum engine.** A deliberately *opposite* selection criterion: it ranks
  coins by **what's actually moving** — relative strength vs BTC, breakouts, volume expansion,
  and acceleration — to surface the volatile movers the trend engine avoids and loses on. Live
  only for now (no momentum backtest yet), with a breakout/ATR trade plan per surfaced mover.

> **It never places trades.** Every output is advisory. The signals are heuristic and their
> edge is **unproven** — the trend engine only after a large backtest, and the momentum engine
> not at all yet (its backtest is a deferred follow-up). Past performance ≠ future results.

---

## Requirements

- **Python ≥ 3.11**
- **[uv](https://docs.astral.sh/uv/)** (manages the virtualenv and dependencies)
- Network access to Binance and TradingView (for live `scan` and for fetching history)

No API keys are needed — it uses Binance public market data and TradingView's public rating.

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

---

## Commands

### `scan` — live signals right now
Scores each watchlist coin from live Binance + TradingView and prints the actionable setups.

```bash
uv run trader scan [--config FILE] [--all] [--details] [--json OUT] [--watch SECONDS]
```
- default view: the **threshold-filtered short list** — only coins with a direction and a
  score ≥ the quality threshold, ranked, with entry/SL/TP/R:R.
- `--all` — show every coin, including sub-threshold and no-direction ones, with the reason a
  coin didn't qualify (`lead_unresolved`, `filter_opposed`, `btc_veto`, …) and a limited-history marker.
- `--details` — expand the per-category score breakdown.
- `--json OUT` — also write the full result as JSON.
- `--watch SECONDS` — re-run on a timer until Ctrl-C.

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
uv run trader movers [--config FILE] [--all] [--json OUT]
```
- default view: the **surfaced short list** — liquid coins scoring ≥ `momentum_threshold`,
  ranked by score descending, with direction and entry/SL/TP/R:R.
- `--all` — show every evaluated coin, including below-threshold and liquidity-filtered ones,
  with a status column (`surfaced` / `below-threshold` / `illiquid`).
- `--json OUT` — write the full report (per-coin scores, factor breakdown, trade plans, the
  once-per-run BTC benchmark, and the advisory disclaimer) derived from the same result.

> It fetches the **BTC benchmark return once** per run, then live daily candles + one
> order-book snapshot per coin — no dependence on the historical cache. It reuses the trend
> engine's `min_depth` / `max_spread` liquidity filter and `atr_buffer` / `target_rr` / `long_only`
> settings, plus its own momentum keys (below). **Momentum entries are often late (near tops);
> its edge is unproven pending a backtest** — treat the output as a shortlist to investigate.

### `backtest` — measure the edge on history
Replays the exact live engine over historical candles (no look-ahead) and reports win rate,
expectancy, profit factor, max drawdown, and per-coin / per-direction breakdowns.

```bash
uv run trader backtest [--config FILE] [--from DATE] [--to DATE] [--json OUT] [--csv OUT] [--demo]
```
- `--from` / `--to` — ISO dates (e.g. `2022-01-01`); only trades entered in the range count,
  earlier candles warm up the indicators. Defaults to the last 365 days.
- `--json OUT` — summary + full per-trade log; `--csv OUT` — the trade log.
- `--demo` — replay a built-in synthetic history (network-free smoke test).
- Without `--demo` it fetches real candles (paginated + cached; see **Caching**).

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

# Scoring (0–100); signals surface at/above the threshold
quality_threshold: 75
category_weights:           # must be the 8 categories; defaults shown
  trend: 25
  structure: 20
  momentum: 15
  volume: 15
  breakout: 10
  btc_alignment: 5
  liquidity: 5
  risk_reward: 5

# Indicator / liquidity / risk knobs (shared by both scanners)
relative_volume_multiple: 1.5
min_depth: 5000.0          # order-book depth floor as quote/USDT NOTIONAL (depth × price,
                           # top-20 levels) — unit-consistent across coins; lower to ~2500
                           # to include thinner movers
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
- **Rate limits** — `scan` leans on TradingView and will throttle on big watchlists.
- Nothing here is financial advice. Don't risk money you can't lose.

---

## Development

```bash
uv run pytest              # test suite
uv run ruff check          # lint
uv run mypy src            # type check (strict)
uv run python -m compileall src
```

The analysis core (`indicators`, `structure`, `direction`, `scoring_model`, `trade_planner`,
`momentum`, `movers`, `backtester`, `concurrent_loader`, `cache_refresher`, …) is kept free of
`typer`/`rich` so it can be reused by other interfaces; `tests/test_core_independence.py`
enforces this.
