# Tasks: Batched TradingView Fetch

Feature slug: `batched-tradingview-fetch`
Requirements: `.sdd/batched-tradingview-fetch/requirements.md`

Each task is a vertical slice through the scan's TradingView-fetch path with observable behavior. Tasks are in dependency order. New settings use in-code defaults in the task that introduces them, then move to YAML where noted. A hard requirement across the feature: batched scan scores are **identical** to the current per-coin scan. Quality gates are identical and deterministic: test suite, linter, type checker, byte-compile.

---

## Task 01-batched-fetch-in-scan

Replace the scan's per-(coin, timeframe) TradingView calls with one batched call per timeframe covering all watchlist symbols, and prove it changes nothing but request volume. Add a batch method to the provider (backed by `get_multiple_analysis`, with an injectable underlying fetch and a pure request/response mapper), and rewire the scan to pre-fetch per interval before the per-coin loop, feeding the unchanged aggregation. Scores and surfaced setups stay identical; TradingView is queried once per timeframe instead of once per coin per timeframe.

### Implementation steps

- [x] Add `get_analysis_batch(symbols, exchange, screener, interval) -> dict[str, TimeframeResult]` to the `AnalysisProvider` Protocol and `TradingViewProvider`, keyed by plain watchlist symbol; keep the single `get_analysis`. Back it with an **injectable** underlying fetch (default a lazily-imported wrapper around `get_multiple_analysis`) so tests need no network.
- [x] Implement the pure mapping: build request symbols as `EXCHANGE:SYMBOL`, apply the existing interval mapping, and map the response back to plain-symbol-keyed `TimeframeResult`s (reusing `map_summary`); omit symbols absent from the response.
- [x] Rewire `run_analysis` to pre-fetch TradingView once per configured timeframe into an `{interval: {symbol: result}}` map before the per-coin loop, with per-interval try/except (one WARNING + empty result on failure) and the existing per-source delay between interval calls; the per-coin loop looks its results up and feeds the unchanged `_aggregate_tradingview`. Remove the per-coin TradingView calls.
- [x] Add unit tests for the pure mapper, the once-per-interval fetch, scan-scoring equivalence, and interval-degradation; extend the core-independence test only if the module surface changed.

### Acceptance criteria

- [x] Given a canned `get_multiple_analysis`-shaped response, `get_analysis_batch` returns plain-symbol-keyed `TimeframeResult`s; request symbols are built as `EXCHANGE:SYMBOL` (a lowercase watchlist symbol is uppercased), the interval is mapped (`1w`→`1W`), and a symbol absent from the response is omitted from the result (tested).
- [x] `run_analysis` invokes the provider's **`get_analysis_batch`** exactly once per configured timeframe (== T calls), and makes **zero** per-coin `get_analysis` calls, over a multi-coin watchlist — verified with a call-recording fake (tested).
- [x] Scan-scoring equivalence (self-contained reference): from a fixed `{(symbol, interval): recommendation}` table driving a batch fake, `run_analysis` yields, per coin, `tv_value == mean(LABEL_VALUES[rec])` over that coin's configured intervals, and per-coin `score`/surfaced setups equal values computed directly from that same table (tested — no reliance on the removed per-coin path or the v1 `ScoringEngine`).
- [x] A whole-interval batch failure logs a single warning and degrades every coin to no TradingView contribution for that interval, and the scan still completes with Binance-only scores for that interval (tested).
- [x] A symbol missing from one interval's batch response loses only its own contribution for that interval while other coins are unaffected (tested).
- [x] No regression: the single `get_analysis` is retained unchanged and the v1 `Runner.run` tests still pass; `provider.py` still imports with `typer` and `rich` blocked (core-independence test passes).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.

---

## Task 02-chunking-retry-config

Harden the batch fetch for large watchlists and transient failures. Split the symbol list into safe chunks per request (configurable), retry a transient batch failure a bounded number of times, and expose the chunk size via YAML config. A watchlist larger than the cap fans out into multiple merged calls; a brief TradingView blip is absorbed instead of dropping a whole timeframe.

### Implementation steps

- [x] Chunk the symbols into groups of `<= tradingview_batch_size` per underlying fetch call inside `get_analysis_batch`, merging the per-chunk results; apply the existing per-source delay between chunk calls.
- [x] Add a bounded retry with backoff (injectable sleep) around the underlying batch fetch; on final failure propagate so the runner degrades that interval.
- [x] Add a `tradingview_batch_size` configuration setting (default 100) with validation (`>= 1`) and unknown-key rejection; thread it into the scan's batch calls.
- [x] Add unit tests for chunking, retry, and config validation.

### Acceptance criteria

- [x] A watchlist whose size is **not** a multiple of the cap (e.g. N=250, size=100) splits into `ceil(N / size)` = 3 underlying fetch calls per interval (distinguishing ceil from floor), and the merged result covers every requested symbol present in the responses — verified with a call-counting fake (tested).
- [x] An underlying fetch that fails transiently then succeeds is retried and the interval is not dropped (injected sleep, no wall-clock); one that always fails causes `get_analysis_batch` to raise so the runner degrades that interval (both tested).
- [x] `tradingview_batch_size` parses with default 100; a value below 1 raises a clear configuration error; an unknown config key is rejected (tested).
- [x] With the configured batch size, a scan over a watchlist exceeding it chunks the requests end-to-end while still producing a rating for every returned symbol (tested).

### Quality gates

- [x] `uv run pytest` passes.
- [x] `uv run ruff check` reports no violations.
- [x] `uv run mypy src` passes.
- [x] `uv run python -m compileall src` reports no errors.
