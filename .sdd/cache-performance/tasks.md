# Tasks: Cache Performance — Daily Refresh & Concurrent Fetch

Feature slug: `cache-performance`
Requirements: `.sdd/cache-performance/requirements.md`

Each task is a vertical slice through the data-fetch/cache layer with observable behavior. Tasks are in dependency order. New settings use in-code defaults in the task that introduces them, then are wired to YAML config where noted. The analysis/scoring/backtest logic is unchanged — a hard requirement is that speeding up the fetch changes no numbers. Quality gates are identical and deterministic: test suite, linter, type checker, byte-compile.

---

## Task 01-thread-safe-completed-candle-provider

Make the historical provider safe to call concurrently and correct about the candle boundary — the prerequisites for parallel fetching. Replace the single shared exchange client with a thread-local one (so concurrent fetches don't share mutable client state), and persist/return only *completed* candles: the still-forming candle for the current period is never stored or returned as final, decided against an injected clock.

Note: this is a prerequisite-heavy slice — the thread-local client has no observable effect until Task 02 adds concurrency, and the completed-candle change is observable only through the `backtest` command (the sole consumer of the historical provider; `market-data` uses a different provider). The equivalence proof in Task 02 depends on this completed-candle boundary, otherwise the forming bar would make sequential vs concurrent runs diverge.

### Implementation steps

- [x] Replace the single lazily-built `ccxt` client with a thread-local client so each worker thread builds and reuses its own; a single provider instance becomes safe under concurrent `get_history` calls.
- [x] Add an injected clock (epoch-ms, default a real clock) and drop any candle whose period has not fully elapsed (`open_ts + timeframe_duration > now`) before merging/writing the cache and before returning candles.
- [x] Keep the existing incremental cache and retry-with-backoff behavior intact.
- [x] Add unit tests for thread-local client use under concurrent calls, completed-candle-only filtering via the injected clock, and preservation of the existing cache/retry behavior; extend the core-independence test if the module surface changed.

### Acceptance criteria

- [x] Called concurrently from multiple threads, the provider uses a distinct client per thread (no shared-client hazard) and returns correct candles for each (tested).
- [x] With the injected clock, a fetched series whose newest row is still within its forming period excludes that row from both the returned and the persisted candles; a fully-closed newest row is included (tested).
- [x] The existing incremental-cache (delta-only on warm re-fetch) and retry-with-backoff behavior still pass their tests unchanged (no regression).
- [x] `historical_data` still imports with `typer` and `rich` blocked (core-independence test passes).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 02-concurrent-loader-and-backtest

Fetch history concurrently and prove it changes nothing but speed. Add a `ConcurrentHistoryLoader` that fans out per-(symbol, timeframe) fetches over a bounded thread pool, assembles per-symbol histories deterministically, and collects per-symbol failures; then have the backtester pre-fetch all coins (and BTC context) through it before replaying. The number of workers is configurable, and the concurrent path must yield backtest results identical to the old sequential path.

### Implementation steps

- [x] Implement `ConcurrentHistoryLoader.load(provider, symbols, timeframes, start, end, *, max_workers)` returning assembled per-symbol histories plus a skipped-coins collection, using a `ThreadPoolExecutor` bounded by `max_workers`, with deterministic (order-independent) assembly.
- [x] Add a `max_workers` configuration setting (default 5) with validation (>= 1).
- [x] Rewire the backtester's provider-sourced run to pre-fetch all watchlist coins and the BTC context via the loader (concurrently), then replay each coin sequentially; preserve the skip-a-failing-coin behavior (now sourced from the loader).
- [x] Add unit tests for real concurrency, deterministic assembly, failure isolation, sequential-vs-concurrent backtest equivalence, and config validation; extend the core-independence test for the new module.

### Acceptance criteria

- [x] With more tasks submitted than `max_workers`, a barrier/counter fake shows observed **peak concurrency equals `max_workers`** (the bound is saturated *and* never exceeded) and all tasks still complete (tested). This concurrency assertion — not a wall-clock timing — is how "materially faster" is proven deterministically.
- [x] The assembled per-symbol histories are identical regardless of the order in which tasks complete (tested).
- [x] A task that fails after retries results in that symbol recorded as skipped while the others assemble normally (tested).
- [x] Equivalence (operational): for the same fake provider, the loader's assembled per-symbol histories equal those from a direct sequential `get_history` loop; and a backtest over that history produces a report whose win rate, expectancy, profit factor, max drawdown, and full trade list/count match a stored golden result (tested).
- [x] `max_workers` parses with default 5; a value below 1 raises a clear configuration error (tested).
- [x] `ConcurrentHistoryLoader` imports with `typer` and `rich` blocked (core-independence test passes).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 03-refresh-cache-command

Add the daily top-up so interactive runs stay warm. A `CacheRefresher` incrementally brings the cache up to the latest completed candle for the whole configured watchlist (plus BTC context) × timeframes, fetching concurrently and only the delta since the cache, bounded on a cold start by a configurable history horizon. A new `refresh-cache` command runs it and prints a summary, intended to be scheduled externally (cron/launchd).

### Implementation steps

- [x] Implement `CacheRefresher.refresh(config, provider, loader, *, now)` that tops up the cache for the watchlist + BTC context across the configured timeframes to the latest completed candle, fetching only the delta since the cache, and returns a summary (coins refreshed, candles added, skipped coins).
- [x] Add a `history_horizon` configuration setting (default ~2 years) bounding the cold-start fill, with validation; wire `max_workers` into the refresh path.
- [x] Add a `refresh-cache` CLI command (`--config`, optional horizon override) that runs the refresher and prints the summary; document scheduling via cron/launchd (no in-app scheduler).
- [x] Add unit tests for delta-only top-up, completed-candle-only persistence, failing-coin skip+report, summary counts, cold-start horizon bound, and config validation; add a light CLI test; extend core-independence.

### Acceptance criteria

- [x] On a fully-warm cache (already at the latest completed candle) refresh makes **zero** fetch calls; on a warm-with-gap cache it fetches only from `cache_end + one interval` onward — both verified with a call-counting fake (tested).
- [x] After refresh, the cache ends at the latest completed candle and the forming candle is not persisted (injected clock) (tested); a subsequent backtest over the refreshed range does no additional fetching for already-cached candles (warm-read, tested).
- [x] A coin that keeps failing is reported as skipped and the refresh completes for the rest (tested).
- [x] The summary reports coins refreshed, candles added, and skipped coins accurately (tested).
- [x] On a cold cache, the fill goes back no further than `history_horizon`; an invalid horizon raises a clear configuration error (both tested).
- [x] The `refresh-cache` command runs against a fake provider (no network), prints the summary, and exits 0 (tested).
- [x] `refresh-cache --help` describes scheduling the command via cron/launchd (documentation present, tested).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.
