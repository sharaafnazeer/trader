"""Concurrent fan-out fetching of per-``(symbol, timeframe)`` history.

The cold fetch of deep history is dominated by network round-trips against a rate-limited
exchange. This module fans those fetches out over a bounded thread pool — one task per
``(symbol, timeframe)`` — so several coins/timeframes download at once instead of strictly
one after another, while a ``max_workers`` bound keeps the exchange's rate limits happy.

The provider seam (:class:`~trader.historical_data.HistoricalDataProvider`) is unchanged:
each task calls ``provider.get_history`` on a worker thread. The concrete provider is
thread-safe (a thread-local client, per Task 01), so a single provider instance is shared
across the pool. Results are assembled **deterministically** — grouped by symbol in the
requested order regardless of the order tasks happen to finish — so the concurrent path
yields exactly the candles the old sequential loop did. A symbol whose fetch fails after
the provider's own retries, or that has no candles for any timeframe, becomes a
:class:`SkippedCoin` and is excluded rather than aborting the whole load.

This module belongs to the analysis core; it does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from trader.historical_data import HistoricalDataProvider
from trader.market_data import Candles

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkippedCoin:
    """A symbol excluded from a load/backtest and why (fetch failure or no history).

    Recorded rather than aborting the run, so one bad symbol never silently breaks or
    biases the whole load.
    """

    symbol: str
    reason: str


def _ordered_unique(items: Iterable[str]) -> list[str]:
    """The items in first-seen order with duplicates removed (deterministic)."""
    return list(dict.fromkeys(items))


class ConcurrentHistoryLoader:
    """Loads per-symbol history concurrently over a bounded thread pool.

    Stateless and reusable: one instance can serve many :meth:`load` calls.
    """

    def load(
        self,
        provider: HistoricalDataProvider,
        symbols: Iterable[str],
        timeframes: Iterable[str],
        start: int,
        end: int,
        *,
        max_workers: int,
    ) -> tuple[dict[str, dict[str, Candles]], tuple[SkippedCoin, ...]]:
        """Fetch every ``(symbol, timeframe)`` concurrently; assemble deterministically.

        Submits one task per ``(symbol, timeframe)`` to a ``ThreadPoolExecutor`` bounded by
        ``max_workers`` and waits for all of them. Returns a ``(histories, skipped)`` pair:
        ``histories`` maps each fully-loaded symbol (in the requested order) to its
        per-timeframe :class:`~trader.market_data.Candles`; ``skipped`` lists the symbols
        excluded because a fetch raised (after the provider's retries) or because some
        timeframe returned no candles for the range. Assembly is independent of the order
        tasks finish, so the result is identical to a sequential ``get_history`` loop.
        """

        ordered_symbols = _ordered_unique(symbols)
        ordered_timeframes = _ordered_unique(timeframes)

        logger.info(
            "loading %d symbols × %d timeframes (max_workers=%d)",
            len(ordered_symbols),
            len(ordered_timeframes),
            max_workers,
        )

        futures: dict[tuple[str, str], Future[Candles]] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for symbol in ordered_symbols:
                for timeframe in ordered_timeframes:
                    futures[(symbol, timeframe)] = executor.submit(
                        provider.get_history, symbol, timeframe, start, end
                    )

        # Collect results on this thread (``Future.result`` re-raises the task's error).
        # Iterating in submission order keeps the recorded first-error deterministic.
        results: dict[tuple[str, str], Candles] = {}
        errors: dict[str, Exception] = {}
        for key, future in futures.items():
            symbol, _timeframe = key
            try:
                results[key] = future.result()
            except Exception as exc:  # noqa: BLE001 - one symbol's failure must not abort
                errors.setdefault(symbol, exc)

        histories: dict[str, dict[str, Candles]] = {}
        skipped: list[SkippedCoin] = []
        for symbol in ordered_symbols:
            if symbol in errors:
                reason = f"fetch failed: {errors[symbol]}"
                logger.warning("skipping %s: %s", symbol, reason)
                skipped.append(SkippedCoin(symbol=symbol, reason=reason))
                continue
            frames: dict[str, Candles] = {}
            missing = False
            for timeframe in ordered_timeframes:
                candles = results[(symbol, timeframe)]
                if candles.frame.empty:
                    missing = True
                    break
                frames[timeframe] = candles
            if missing:
                reason = "no historical data available for range"
                logger.warning("skipping %s: %s", symbol, reason)
                skipped.append(SkippedCoin(symbol=symbol, reason=reason))
                continue
            logger.info("loaded %s (%d timeframes)", symbol, len(frames))
            histories[symbol] = frames

        return histories, tuple(skipped)
