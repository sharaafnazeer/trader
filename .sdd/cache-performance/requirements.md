# Requirements: Cache Performance — Daily Refresh & Concurrent Fetch

> Builds on the `signal-edge-backtester` feature. Backtesting works, but the first
> ("cold") fetch of deep history is slow: candles are fetched strictly sequentially
> (one coin, one timeframe, one page at a time) against a rate-limited exchange, so a
> multi-year, multi-coin backtest can take an hour on first run. This feature makes the
> cold fetch concurrent and adds a daily cache-refresh command so interactive runs are
> effectively instant. It changes only the data-fetch/cache layer; the analysis engine,
> scoring, and backtest logic are unchanged.

## Problem Statement

As a crypto trader, running a backtest over a real watchlist for the first time is painfully slow — it can take the better part of an hour because the tool downloads years of candles one request at a time from a rate-limited exchange, coin by coin, timeframe by timeframe. Historical candles for closed periods never change, yet nothing keeps them ready between runs in a way that stays fresh, so every new date range or new machine pays the full cold-download cost again. I want backtests and scans to feel fast day to day, without re-downloading history I already have and without standing up extra infrastructure.

## Solution

Fetch historical candles concurrently — several coins/timeframes at once instead of strictly one after another — so the one-time cold download completes several times faster (bounded to stay within the exchange's rate limits). And add a daily cache-refresh command that tops up the on-disk cache to the latest completed candle for the whole watchlist, so it can be scheduled (e.g. via cron) to run once a day; after that, interactive backtests and scans read warm data and only ever fetch the small, still-forming part of the current period. Because closed candles are immutable, the cache only stores completed candles and never a half-formed one, so results stay correct. No new services or databases are introduced — it's the existing file cache, filled in parallel and kept fresh on a schedule.

## User Stories

1. As a trader, I want historical candles fetched concurrently, so that the first cold backtest finishes in minutes instead of an hour.
2. As a trader, I want concurrency bounded to a safe number of workers, so that parallel fetching does not trip the exchange's rate limits.
3. As a trader, I want the number of concurrent workers to be configurable, so that I can tune it to my connection and rate-limit headroom.
4. As a trader, I want a command that refreshes the cache for my whole watchlist, so that I can schedule it to run once a day.
5. As a trader, I want the daily refresh to only download what is missing since the last run, so that it is quick and does not re-download history I already have.
6. As a trader, I want the refresh to top the cache up to the most recent completed candle, so that a later backtest or scan reads warm data.
7. As a trader, I want the cache to store only completed candles and never a still-forming one, so that cached results are correct and stable.
8. As a trader, I want the refresh to cover every coin and timeframe my configuration uses, so that nothing is left cold.
9. As a trader, I want a coin that fails to refresh to be reported and skipped, so that one bad symbol never aborts the whole refresh.
10. As a trader, I want concurrent fetching to produce exactly the same candles and backtest results as the old sequential path, so that speeding it up changes nothing about the numbers.
11. As a trader, I want a bound on how far back the cache is maintained on a cold start, so that the first fill is predictable and not unbounded.
12. As a trader, I want the refresh command to print a short summary (coins refreshed, candles added, any skips), so that I can confirm it ran and see what it did.
13. As a trader, I want to keep using the existing file cache with no new services to install, so that nothing extra has to be running.
14. As a trader, I want repeated backtests over already-cached ranges to do little or no fetching, so that iterating on settings is fast.
15. As a trader, I want the concurrent fetch to retry transient errors and skip a coin only after retries are exhausted, so that speeding things up does not make it more fragile.

## User Acceptance Tests

1. Given a cold cache and a multi-coin watchlist, when a backtest runs, then the candles for different coins/timeframes are fetched concurrently (not strictly one at a time) and the run completes materially faster than the sequential equivalent.
2. Given a configured worker limit, when history is fetched concurrently, then no more than that many fetches are in flight at once.
3. Given the same watchlist and date range, when the same history is fetched sequentially and concurrently, then the assembled candles are identical.
4. Given a warm cache and the refresh command, when it runs again, then it only fetches candles newer than what is already cached (little or nothing to download).
5. Given the refresh command, when it completes, then the cache holds candles up to the most recent completed candle for every configured coin and timeframe.
6. Given the current period's candle is still forming, when the cache is written, then that in-progress candle is not stored as a completed candle.
7. Given one coin whose fetch keeps failing, when the refresh runs, then that coin is reported as skipped and the refresh still completes for the rest.
8. Given a transient fetch error during concurrent loading, when the fetch is retried, then it succeeds without failing the coin.
9. Given the refresh command finishes, when its summary is shown, then it reports the coins refreshed, candles added, and any skipped coins.
10. Given a cold-start cache with a configured history horizon, when the cache is first filled, then it goes back no further than that horizon.
11. Given a backtest is run after a daily refresh, when it executes, then it reads warm data and only the current forming period (if any) is fetched live.
12. Given concurrent fetching is enabled, when a backtest produces its report, then the win rate, expectancy, and all metrics match what the sequential path produced for the same inputs.

## Definition of Done

- Historical candles are fetched concurrently via a bounded worker pool, and the cold fetch is materially faster than the sequential baseline.
- The number of concurrent workers is configurable with a safe default.
- Concurrent fetching yields candles and backtest results identical to the sequential path for the same inputs.
- A refresh command tops up the cache for the whole configured watchlist × timeframes, incrementally (only fetching what is newer than the cache), up to the most recent completed candle, and prints a summary.
- The cache stores only completed candles; the still-forming candle is never persisted as final.
- A coin that fails after retries is reported and skipped; the refresh/backtest completes for the rest.
- A configurable history horizon bounds the cold-start fill.
- The refresh command is runnable on a schedule (cron/launchd); scheduling itself is the user's responsibility and is documented, not managed in-app.
- No new external services or databases are introduced; the existing on-disk cache is used.
- Concurrent access is safe (no corrupted cache files, no shared-client hazards).
- Automated tests cover concurrent loading, the refresh logic, completed-candle-only caching, configuration validation, and sequential-vs-concurrent equivalence; all quality gates pass.
- The analysis engine, scoring, direction rule, and backtest logic are unchanged; no regression in existing tests.

## Out of Scope

- Redis or any in-memory cache server, and any external database (time-series or otherwise). Revisit only if the tool becomes a hosted/multi-user service.
- Switching the cache file format to Parquet (a possible future speed/size optimization; the current CSV format is retained).
- Concurrency for the live `scan` command (its bottleneck is TradingView rate-limiting, a separate concern; scan stays sequential).
- An in-app scheduler/daemon; the daily cadence is achieved by the user scheduling the refresh command externally.
- Any change to indicators, structure, direction, scoring, trade planning, or the backtest simulation/metrics.
- `asyncio`/multiprocessing-based fetching (threads are used).

## Further Notes

- This came out of running a real backtest: a 32-coin, 4-year run was dominated by the one-time cold download of deep history (the 15-minute series alone is ~140k bars per coin, paginated ~1k at a time against a rate-limited endpoint). The incremental cache already avoids re-downloading on warm runs; this feature attacks the cold-fill time and keeps the cache fresh automatically.
- Redis was considered and deliberately rejected for this phase: the access pattern is bulk sequential range reads that files serve well, Redis adds an always-on service and RAM cost, and it would not speed up the actual network fetch. A time-series DB (e.g. TimescaleDB/DuckDB) or Redis is the right move only if this becomes a running service.
- Correctness hinges on the closed-vs-forming candle boundary: only candles whose period has fully elapsed are immutable and cacheable.

---

## Technical Annex
> Written against codebase as of: 2026-07-30

Builds on `src/trader/` (the `signal-edge-backtester` implementation). When generating tasks, verify each decision against the current code and flag conflicts.

### Current state (baseline)

- `historical_data.py` — `HistoricalDataProvider` (Protocol) + `CcxtHistoricalDataProvider`: paginated `fetch_ohlcv` with a moving `since` over `[start − warmup, end]`, an on-disk **CSV** cache keyed by `(symbol, timeframe)` that reads first and fetches only the gap (incremental), and bounded retry-with-backoff (`_fetch_with_retry`, injectable `sleep`). A **single** lazily-built `ccxt` client is held in `_client_box` (one client per provider instance — the concurrency hazard to fix).
- `backtester.py` — `run_from_provider(config, provider, *, start, end)` loops the watchlist **sequentially**, calling `_history_for` (per-timeframe `get_history`) for each coin, then `run_coin`; catches per-coin fetch errors → `SkippedCoin`; BTC context fetched the same way and degrades to neutral.
- `config.py` — frozen `Config` + `BacktestConfig` (`fee_rate`, `slippage`, `risk_per_trade`, `max_holding_bars`, `start`, `end`, `cache_dir`); `load_config` with `ConfigError`, nested unknown-key rejection, typed validators; `parse_iso_date`.
- `cli.py` — `scan` / `market-data` / `backtest` typer commands; `backtest` builds `CcxtHistoricalDataProvider(cache_dir=…)` and calls `run_from_provider`.
- Tooling: Python ≥3.11, `uv`; `pytest`/`ruff`/`mypy --strict`; analysis core free of `typer`/`rich` (enforced by `tests/test_core_independence.py`). 183 tests green.

### Architectural Decisions

**Concurrency mechanism.** `concurrent.futures.ThreadPoolExecutor` (I/O-bound; works with the synchronous `ccxt`; minimal change). No `asyncio`/multiprocessing.

**Thread-safety of the provider (`historical_data.py`).**
- Replace the single `_client_box` with a **thread-local `ccxt` client** so one `CcxtHistoricalDataProvider` instance is safe under concurrent `get_history` calls (each worker thread lazily builds and reuses its own client). Cache writes are per-`(symbol, timeframe)` distinct files → no write contention.
- **Completed-candle-only caching:** persist only candles whose period has fully elapsed. A candle at open `ts` on a timeframe of duration `d` is closed iff `ts + d <= now`. Drop the in-progress bar before writing/merging the cache. `now` is injected (a `clock: Callable[[], int]` returning epoch-ms, default a real clock) so tests are deterministic.

**New module — `ConcurrentHistoryLoader`** (analysis-core, no `typer`/`rich`).
- `load(provider, symbols, timeframes, start, end, *, max_workers) -> tuple[dict[str, dict[str, Candles]], tuple[SkippedCoin, ...]]`
- Submits one task per `(symbol, timeframe)` to a `ThreadPoolExecutor(max_workers)`, awaits all, and **assembles results deterministically** (group by symbol in `symbols` order; a symbol missing any timeframe, or whose task raised after the provider's retries, becomes a `SkippedCoin` and is excluded). Order-independent output.

**New module — `CacheRefresher`** (analysis-core).
- `refresh(config, provider, loader, *, now) -> RefreshSummary` — for the configured watchlist (+ BTC context symbol) × profile timeframes, loads (concurrently, via `ConcurrentHistoryLoader`) from `now − history_horizon` (bounded cold start) through the latest completed candle, topping up the cache incrementally. Returns a `RefreshSummary` (coins refreshed, candles added, skipped list). No live/forming candle persisted.

**Modified — `backtester.py`.** `run_from_provider` pre-fetches all watchlist + BTC histories via `ConcurrentHistoryLoader` (parallel), then runs the existing per-coin replay **sequentially** (replay is pure/fast). Skip-on-failure behavior preserved (now sourced from the loader's `SkippedCoin`s). Backtest numbers must be identical to the sequential path.

**Modified — `config.py`.** Add `max_workers: int` (default 5) and `history_horizon` (default ~2 years, expressed as days or an ISO duration; bounds cold fill) — likely on `BacktestConfig` (or a small `cache`/`fetch` section). Typed validation (`max_workers >= 1`; positive horizon); unknown-key rejection extended.

**Modified — `cli.py`.** New **`refresh-cache`** command: `--config` (+ optional `--from`/horizon override); builds `CcxtHistoricalDataProvider` + `ConcurrentHistoryLoader`, runs `CacheRefresher`, prints the summary. Documented for cron/launchd; no in-app scheduler. `backtest` threads `max_workers` into the loader.

**Data flow (backtest/refresh):** config → `ConcurrentHistoryLoader.load(provider, watchlist(+BTC), timeframes, start/horizon, end, max_workers)` → per-`(symbol,timeframe)` `provider.get_history` on worker threads (thread-local client, retry, closed-candle-only cache) → deterministic assembly + `SkippedCoin`s → (backtest) `run_coin` replay per coin / (refresh) `RefreshSummary`.

**Rejected:** Redis / time-series DB (revisit only for a hosted service), Parquet cache, scan concurrency, async fetch.

### Automated Testing Decisions

Test external behavior; no network — inject a **thread-safe fake fetch/provider** and an injected clock. `pytest`; prior art in `tests/test_historical_data.py` (injected fetch, cache reuse, retry) and `tests/test_backtester_provider.py` (fake provider, skip-and-continue).

- **`ConcurrentHistoryLoader` (unit).** With a fake provider: all `(symbol, timeframe)` tasks complete and assemble into the expected per-symbol frames; **concurrency is real** (e.g. a barrier/latch fake proves ≥2 tasks run simultaneously, and/or a counter proves ≤ `max_workers` in flight); **deterministic assembly** (result identical regardless of task completion order); a task that raises after retries → that symbol in `SkippedCoin`s, others unaffected.
- **`CacheRefresher` (unit).** With a fake provider + injected clock: a warm cache refresh fetches only the delta (call-counting fake); the cache ends at the latest **completed** candle; the forming candle is not persisted; a persistently-failing coin is skipped and reported; the summary counts are correct.
- **`historical_data` (unit).** Thread-local client is used under concurrent calls (no shared-client hazard); with an injected clock, a fetch whose newest row is still-forming is excluded from the persisted/returned candles (completed-candle-only).
- **`config` (unit).** `max_workers` and `history_horizon` parse with defaults; invalid values (`max_workers < 1`, non-positive horizon) raise `ConfigError`; unknown keys rejected.
- **`backtester` (integration, fakes).** The concurrent `run_from_provider` yields **outcomes identical** to the previous sequential path for the same fake history (equivalence), and preserves skip-on-failure.
- **`cli` `refresh-cache` (light).** With a fake provider (monkeypatched, no network), the command runs and prints a summary, exit 0. Rendering internals otherwise untested.
- Core-independence: `ConcurrentHistoryLoader` and `CacheRefresher` import with `typer`/`rich` blocked (extend `tests/test_core_independence.py`).
