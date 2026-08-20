"""TradingView's recommendation labels as numbers.

All that survives of the v1 screener, which ranked a watchlist purely on TradingView's
aggregated recommendation. The scanner that replaced it derives its own indicators; this
table remains only because the recommendation is still fetched and averaged into a single
value in ``[-2, 2]``.
"""

from __future__ import annotations

from trader.provider import (
    BUY,
    NEUTRAL,
    SELL,
    STRONG_BUY,
    STRONG_SELL,
)

# Numeric value assigned to each recommendation label.
LABEL_VALUES: dict[str, int] = {
    STRONG_BUY: 2,
    BUY: 1,
    NEUTRAL: 0,
    SELL: -1,
    STRONG_SELL: -2,
}
