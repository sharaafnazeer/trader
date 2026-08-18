"""Daily cache top-up so interactive backtests and scans read warm data.

Historical candles for closed periods never change, so the on-disk cache only ever needs
to grow forward: from wherever it currently reaches, up to the most recent *completed*
candle. :class:`CacheRefresher` drives exactly that for the whole configured watchlist
(plus the BTC context symbol) across the configured timeframes. It reuses the existing
seams — the incremental, completed-candle-only
:class:`~trader.historical_data.CcxtHistoricalDataProvider` and the bounded
:class:`~trader.concurrent_loader.ConcurrentHistoryLoader` — so a refresh:

* fetches **only the delta** since the cache (a fully-warm cache fetches nothing);
* is bounded on a cold start by ``history_horizon`` (it never fills further back than
  ``now - history_horizon_days``), so the first fill is predictable;
* fetches concurrently (``max_workers`` from config) to keep the cold fill fast; and
* stores only completed candles — the still-forming current-period candle is never
  persisted — because that boundary is enforced by the provider against its clock.

The refresh is meant to be scheduled externally (cron/launchd); there is no in-app
scheduler. It returns a :class:`RefreshSummary` (coins refreshed, candles added, coins
skipped) so the caller can confirm what happened.

This module belongs to the analysis core; it does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from trader.backtester import BTC_CONTEXT_SYMBOL
from trader.concurrent_loader import ConcurrentHistoryLoader, SkippedCoin
from trader.config import Config
from trader.historical_data import HistoricalDataProvider

logger = logging.getLogger(__name__)

# Milliseconds in one day; the history horizon is configured in days.
_MS_PER_DAY = 86_400_000


def _real_clock() -> int:
    """Current wall-clock time as epoch milliseconds (the default ``now``)."""
    return int(time.time() * 1000)


@dataclass(frozen=True)
class RefreshSummary:
    """The outcome of a cache refresh: what was topped up, added, and skipped.

    ``coins_refreshed`` is the number of symbols (watchlist + BTC context) brought up to
    date; ``candles_added`` is how many new completed candles were written to the cache
    across all symbols/timeframes this run (zero on a fully-warm cache); ``skipped`` lists
    the symbols whose fetch failed after retries, so one bad symbol never aborts the run.
    """

    coins_refreshed: int
    candles_added: int
    skipped: tuple[SkippedCoin, ...] = ()


def _count_cached_rows(cache_dir: Path) -> int:
    """Total candle rows currently held across every CSV cache file under ``cache_dir``.

    Used to measure how many candles a refresh added (the after-minus-before delta). Files
    are counted format-agnostically (any ``*.csv``), so the count does not depend on the
    provider's symbol-to-filename scheme. A missing directory or an empty/unreadable file
    contributes zero.
    """

    if not cache_dir.exists():
        return 0
    total = 0
    for path in sorted(cache_dir.glob("*.csv")):
        try:
            frame = pd.read_csv(path)
        except (pd.errors.EmptyDataError, OSError):
            continue
        total += len(frame)
    return total


class CacheRefresher:
    """Tops up the on-disk cache for the configured watchlist to the latest closed candle.

    Stateless and reusable: one instance can serve many :meth:`refresh` calls.
    """

    def refresh(
        self,
        config: Config,
        provider: HistoricalDataProvider,
        loader: ConcurrentHistoryLoader,
        *,
        now: int | None = None,
    ) -> RefreshSummary:
        """Bring the cache up to the latest completed candle; return a summary.

        Loads the watchlist plus the BTC context symbol, across ``config.timeframes``,
        from ``now - history_horizon`` through ``now`` — concurrently, bounded by
        ``config.backtest.max_workers`` — via the injected ``loader`` and ``provider``.
        The provider fetches only the delta beyond what the cache already holds (nothing on
        a fully-warm cache) and persists only completed candles, so the horizon bounds the
        cold-start fill and the still-forming candle is never stored. ``now`` (epoch-ms) is
        injected for deterministic tests and defaults to a real clock. A symbol whose fetch
        fails after retries is recorded in the returned :class:`RefreshSummary` rather than
        aborting the refresh for the rest.
        """

        now_ms = _real_clock() if now is None else now
        start = now_ms - config.backtest.history_horizon_days * _MS_PER_DAY
        end = now_ms

        symbols = [*config.watchlist, BTC_CONTEXT_SYMBOL]
        cache_dir = Path(config.backtest.cache_dir)

        logger.info(
            "refreshing cache for %d coins across %d timeframes over [%d, %d]",
            len(symbols),
            len(config.timeframes),
            start,
            end,
        )

        before = _count_cached_rows(cache_dir)
        histories, skipped = loader.load(
            provider,
            symbols,
            config.timeframes,
            start,
            end,
            max_workers=config.backtest.max_workers,
        )
        after = _count_cached_rows(cache_dir)

        for symbol, frames in histories.items():
            loaded = sum(len(candles.frame) for candles in frames.values())
            logger.info(
                "refreshed %s: %d candles across %d timeframes", symbol, loaded, len(frames)
            )

        for coin in skipped:
            logger.warning("skipped %s during refresh: %s", coin.symbol, coin.reason)

        candles_added = max(0, after - before)
        logger.info(
            "cache refresh complete: %d coins refreshed, %d candles added, %d skipped",
            len(histories),
            candles_added,
            len(skipped),
        )

        return RefreshSummary(
            coins_refreshed=len(histories),
            candles_added=candles_added,
            skipped=skipped,
        )
