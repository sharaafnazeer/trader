# Requirements: Batched TradingView Fetch

> A performance fix for the live `scan`. Today the scan queries TradingView once per
> coin per timeframe — hundreds of tiny requests that trigger heavy rate-limiting (HTTP
> 429) and make a large-watchlist scan take ~20 minutes. TradingView supports fetching
> many symbols for one timeframe in a single request; this feature switches the scan to
> that batched call, cutting hundreds of requests to a handful and eliminating the
> rate-limit problem — with identical scores. It touches only the scan's TradingView
> fetch; the backtester (which never calls TradingView), scoring, and Binance fetching
> are unchanged.

## Problem Statement

As a crypto trader, scanning my watchlist is painfully slow and noisy. For every coin the
tool makes a separate TradingView request for each timeframe, so an 87-coin scan fires
hundreds of tiny requests at TradingView. TradingView rate-limits aggressively, so the
console fills with "429 Too Many Requests" warnings and the scan drags on for the better
part of twenty minutes — even though most of that time is just waiting between throttled
requests. I want the scan to be fast and quiet without changing what it actually decides.

## Solution

Fetch TradingView's ratings in **batches**: one request per timeframe that asks for every
watchlist symbol at once, instead of one request per coin per timeframe. That turns
hundreds of requests into a handful (one per timeframe), so the rate-limiting essentially
disappears and the TradingView part of the scan finishes in seconds instead of minutes —
the scan becomes limited only by the (fast) Binance data fetch. The ratings that feed the
score are exactly the same, so **the scores and surfaced setups are identical** to before;
only the speed and the request volume change. Very large watchlists are split into safe
chunks automatically, and if TradingView is briefly unavailable the scan still completes,
scoring those coins on the Binance-derived factors alone (as it already does today).

## User Stories

1. As a trader, I want the scan to fetch all watchlist symbols for a timeframe in one request, so that it makes a handful of requests instead of hundreds.
2. As a trader, I want the scan to stop flooding TradingView with per-coin requests, so that I rarely see rate-limit (429) warnings.
3. As a trader, I want the scan to finish in a couple of minutes instead of ~20, so that I can use it interactively.
4. As a trader, I want the batched scan to produce exactly the same scores and surfaced setups as before, so that speeding it up changes nothing about the decisions.
5. As a trader, I want a very large watchlist to be split into safe chunks automatically, so that a big list doesn't break or get silently truncated.
6. As a trader, I want the chunk size to be configurable, so that I can tune it if needed.
7. As a trader, I want a briefly-unavailable TradingView to be retried a couple of times, so that a transient blip doesn't drop the rating for a whole timeframe.
8. As a trader, I want a timeframe whose TradingView batch still fails to degrade gracefully, so that every coin is still scored (on Binance factors) and the scan completes.
9. As a trader, I want a coin that TradingView doesn't return in the batch to simply lose its TradingView contribution for that timeframe, so that one odd symbol doesn't affect the others.
10. As a trader, I want all the configured timeframes still consulted, so that the TradingView contribution to the score is unchanged.
11. As a trader, I want the backtester and the rest of the pipeline untouched, so that this change carries no risk to anything but scan speed.
12. As a trader, I want to be able to lower the TradingView delay afterward, so that with so few requests the scan is even faster.

## User Acceptance Tests

1. Given a watchlist of N coins and T configured timeframes, when a scan runs, then TradingView is queried once per timeframe (T requests) rather than once per coin per timeframe (N×T requests).
2. Given the same watchlist and market data, when a scan runs with batched fetching versus the old per-coin fetching, then every coin's score and the surfaced short list are identical.
3. Given a large watchlist that exceeds the configured chunk size, when a scan runs, then the symbols are split into chunks and every coin still receives its rating (nothing is dropped or truncated).
4. Given TradingView returns a transient error on a batch request, when the scan retries, then the request succeeds and the ratings are used without dropping the timeframe.
5. Given TradingView keeps failing for a timeframe, when the scan runs, then a single warning is logged for that timeframe, every coin is still scored from the Binance factors, and the scan completes.
6. Given TradingView's batch response omits a particular symbol, when scores are computed, then that coin simply has no TradingView contribution for that timeframe while all other coins are unaffected.
7. Given the batched scan, when its console warnings are observed on a large watchlist, then 429 rate-limit warnings are absent or rare (a handful at most), not hundreds.
8. Given a backtest is run, when it executes, then its behavior and results are unchanged by this feature (the backtester does not use TradingView).

## Definition of Done

- The scan fetches TradingView ratings per timeframe in a single batched request covering all watchlist symbols, not per coin per timeframe.
- Batched scan scores and surfaced setups are identical to the pre-change per-coin scan for the same inputs (proven by an equivalence test).
- Watchlists larger than the configured chunk size are split into chunks and merged, with no dropped or truncated symbols; the chunk size is configurable with a safe default.
- A transient TradingView failure on a batch request is retried a small, bounded number of times before giving up.
- A batch request that still fails degrades that timeframe to no TradingView contribution for any coin (logged once); a symbol missing from a response loses only its own contribution for that timeframe. In both cases the scan completes and coins are scored on the remaining factors.
- All configured timeframes are still consulted, so the TradingView contribution to the score is unchanged.
- Request volume drops from ~N×T to ~T (plus chunking), and 429 warnings are effectively eliminated on a large watchlist.
- The backtester, scoring/direction/trade-planner logic, Binance fetching, and the v1 per-coin path are unchanged; no regression in existing tests.
- Automated tests cover the batch request/response mapping, chunking, retry, graceful degradation, scan-scoring equivalence, and configuration validation; all quality gates pass.

## Out of Scope

- Batching or otherwise changing the **Binance** fetch (candles + order book) — Binance has no equivalent multi-symbol endpoint and is not the bottleneck; it stays per-symbol.
- Any change to scoring, direction, trade planning, or the backtester (the backtester does not call TradingView at all).
- The v1 per-coin `scan` path / single-symbol rating call — retained as-is.
- Concurrency for the scan (a separate concern; batching already removes the request volume that caused the slowness).
- Persisting/caching TradingView ratings (they are live, per-scan).
- Removing or re-tuning the existing per-request delay (the delay knob remains; lowering it is a user config choice, not part of this change).

## Further Notes

- Root-cause fix, not a workaround: earlier we raised the inter-request delay to 3.5s to reduce 429s, which merely traded warnings for a ~20-minute scan. Batching removes the request *volume* that causes the limit, so both the warnings and the wait go away, and the delay can later be lowered.
- The library (`tradingview-ta` 3.3.0, already installed) provides `get_multiple_analysis(screener, interval, symbols)`, confirmed present — one request returns all symbols' analysis for one interval.
- Only `scan` uses live TradingView; `backtest` runs Binance-only, so it is entirely unaffected and needs no changes.

---

## Technical Annex
> Written against codebase as of: 2026-07-31

Modifies the `signal-edge-backtester`/`multi-factor-scoring-engine` codebase under
`src/trader/`. When generating tasks, verify each decision against the current code.

### Current state (baseline)

- `provider.py` — `AnalysisProvider` Protocol with `get_analysis(symbol, exchange, screener, interval) -> TimeframeResult`; concrete `TradingViewProvider.get_analysis` lazily imports `TA_Handler` and calls `map_summary(symbol, interval, analysis.summary)`; `to_tradingview_interval(interval)` maps `1w`→`1W` (others pass through); `map_summary` is the tested pure mapper.
- `runner.py` `run_analysis` — for each coin, loops `for interval in timeframes` and calls `analysis.get_analysis(symbol, exchange, screener, interval)` with a `wait(_SOURCE_TRADINGVIEW, tradingview_delay)` before each; collects `tv_results` and folds them via `_aggregate_tradingview(results) -> float | None` (mean of `LABEL_VALUES` → `[-2, 2]`) into `tv_value`, passed to `ScoringModel.score(..., tradingview=tv_value)`. Per-call exceptions log a WARNING and degrade. TradingView is fetched for **all** configured timeframes.
- `config.py` — frozen `Config` + `load_config` (`ConfigError`, unknown-key rejection, `_require_int`/typed validators); has `tradingview_delay`, `binance_delay`, `ohlcv_lookback`, etc.
- Tooling: Python ≥3.11, `uv`; `pytest`/`ruff`/`mypy --strict`; analysis core free of `typer`/`rich` (enforced by `tests/test_core_independence.py`). 215 tests green. `tradingview_ta==3.3.0` exposes `get_multiple_analysis(screener, interval, symbols, additional_indicators=[], timeout=None, proxies=None)`.

### Architectural Decisions

**Provider (`provider.py`) — additive batch method.**
- Add to the `AnalysisProvider` Protocol and `TradingViewProvider`:
  `get_analysis_batch(symbols, exchange, screener, interval) -> dict[str, TimeframeResult]`, keyed by the plain watchlist symbol. Keep the single `get_analysis` unchanged (used by v1 `run` + its tests).
- **Injectable underlying fetch** (mirrors `historical_data`'s injectable `fetch`): a field like `multi_fetch: Callable[[str, str, list[str]], dict[str, Any]] | None = None`; when `None`, a lazily-imported default wraps `tradingview_ta.get_multiple_analysis(screener, interval, symbols)`. Tests inject a fake — no network.
- **Pure mapping helper** (the tested part, analogous to `map_summary`): builds request symbols `f"{exchange}:{symbol}"` (uppercased), applies `to_tradingview_interval(interval)`, and maps the `get_multiple_analysis` response (`{"EXCHANGE:SYMBOL": Analysis}`) back to `{plain_symbol: TimeframeResult}` via `map_summary(symbol, interval, analysis.summary)`. Symbols absent from the response are omitted.
- **Chunking:** split `symbols` into chunks of `<= tradingview_batch_size` (default 100), one `multi_fetch` per chunk, merge; sleep `tradingview_delay` between chunk calls (reuse the injectable sleep). Merged result covers all requested symbols present in responses.
- **Light retry:** bounded retry (default ~2) with backoff on a `multi_fetch` exception, using an injectable sleep, before propagating — because a batch failure now affects a whole interval. On final failure the caller degrades the interval.

**Runner (`runner.py`) — pre-fetch per interval.**
- In `run_analysis`, before the per-coin loop, build `tv_by_interval: dict[str, dict[str, TimeframeResult]]` = for each configured `interval`, `analysis.get_analysis_batch(watchlist, exchange, screener, interval)` guarded by try/except: on failure log one WARNING (`"TradingView batch degraded for interval X: …"`) and store `{}` for that interval; `wait(_SOURCE_TRADINGVIEW, tradingview_delay)` between interval batch calls.
- In the per-coin loop, replace the per-(coin, interval) fetch with a lookup: `tv_results = [tv_by_interval[itv][symbol] for itv in timeframes if symbol in tv_by_interval[itv]]`, then the **unchanged** `_aggregate_tradingview(tv_results)` → `tv_value`. A missing symbol in an interval simply contributes nothing; a fully-degraded set yields `tv_value = None` (Binance-only), exactly as today.
- Remove the per-coin TradingView `get_analysis` calls and their per-call throttle from the coin loop.

**Config (`config.py`).** Add `tradingview_batch_size: int` (default 100) with validation (`>= 1`); extend the unknown-key allow-list. (Retry count may stay an in-code default on the provider.)

**Scope.** Only `run_analysis` (scan) + the provider batch method change. v1 `Runner.run`, `backtester` (no TradingView), scoring/direction/trade-planner, and Binance fetching are untouched. **Scan-scoring equivalence to the current per-coin path is a hard requirement.**

**Data flow (scan, per interval up front):** `run_analysis` → for each interval `analysis.get_analysis_batch(watchlist, …)` → chunk → `multi_fetch`(=lazy `get_multiple_analysis`) → pure map back to `{symbol: TimeframeResult}` → assemble `tv_by_interval`. Then per coin: gather its per-interval results → `_aggregate_tradingview` → `tv_value` → `ScoringModel.score(...)` (unchanged downstream).

### Automated Testing Decisions

Test external behavior with pinned inputs; no network — inject a fake `multi_fetch` and sleep. `pytest`; prior art: `tests/test_provider.py` (`map_summary` canned mapping), `tests/test_historical_data.py` (injectable fetch, chunking-style pagination, retry), `tests/test_runner_analysis.py` (fake providers, degradation), `tests/test_config_backtest.py` (config validation).

- **Pure mapper (unit).** Canned `get_multiple_analysis`-shaped response → asserts request symbols are `EXCHANGE:SYMBOL`, the interval is mapped (`1w`→`1W`), the result is keyed by plain symbols with correct `TimeframeResult`s, and a symbol absent from the response is omitted.
- **Chunking (unit).** A watchlist larger than `tradingview_batch_size` → the injected `multi_fetch` is called once per chunk (call-counting fake) and the merged result covers all symbols.
- **Retry (unit).** `multi_fetch` that fails transiently then succeeds → the batch succeeds (injected sleep, no wall-clock); one that always fails → the method raises (so the runner degrades the interval).
- **Runner (integration, fake provider).** A fake `AnalysisProvider` implementing `get_analysis_batch`: assert TradingView is fetched **once per interval, not per coin** (call recorder); **equivalence** — the resulting `tv_value`/scores/surfaced setups equal those produced when the same underlying per-(symbol, interval) recommendations are supplied (reference); a whole-interval batch failure degrades all coins for that interval to Binance-only; a coin missing from one interval still scores on the others.
- **Config (unit).** `tradingview_batch_size` parses with default 100; `< 1` raises `ConfigError`; unknown keys rejected.
- **Core-independence.** `provider.py` still imports with `typer`/`rich` blocked (unchanged; extend the test only if the surface changed).
- The concrete network `get_multiple_analysis` shim is not unit-tested (thin, network-bound), consistent with the untested `TA_Handler` path in `get_analysis`.
