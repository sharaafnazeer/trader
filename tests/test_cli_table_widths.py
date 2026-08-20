"""The setups table must never silently drop digits from a trade level.

Rich ellipsizes an over-full column, so at 80 columns a stop-loss of ``0.00267387`` printed
as ``0.00267…``. That is the one failure mode a trading table cannot have: a truncated
symbol is an annoyance, a truncated stop-loss is a wrong order. These tests pin the two
halves of the fix — price columns fold instead of ellipsizing, and a redirected run gets a
width the table fits in rather than rich's 80-column fallback.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pandas as pd
from rich.console import Console

from trader.cli import NON_TERMINAL_WIDTH, _render_setups
from trader.direction import Direction
from trader.market_data import Candles, OrderBook
from trader.runner import AnalysisRun, CoinAnalysis
from trader.trade_planner import TradePlan

# A sub-cent coin: the case that overflows, because every level needs eight significant
# digits to be actionable at all.
ENTRY = 0.00302
STOP = 0.00267387
TARGET = 0.00371227


def _run() -> AnalysisRun:
    frame = pd.DataFrame(
        [[0, 1.0, 1.0, 1.0, ENTRY, 1.0]],
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    plan = TradePlan(
        direction=Direction.LONG,
        entry=ENTRY,
        stop_loss=STOP,
        take_profit=TARGET,
        risk_reward=2.0,
        invalidation=0.002727,
    )
    analysis = CoinAnalysis(
        symbol="PUMPUSDT",
        direction=Direction.LONG,
        features_by_tf={},
        structure_by_tf={},
        candles_by_tf={"4h": Candles(symbol="PUMPUSDT", timeframe="4h", frame=frame)},
        order_book=OrderBook(
            symbol="PUMPUSDT", best_bid=1.0, best_ask=1.0, bid_depth=1.0, ask_depth=1.0
        ),
        plan=plan,
    )
    return AnalysisRun(analyses=(analysis,), setups=(analysis,))


def _plain(console: Console) -> str:
    """Rendered text with ANSI stripped and cell padding collapsed.

    A folded value wraps across two lines, so the digits are present but not contiguous;
    stripping the table's borders and whitespace is what lets a single assertion cover both
    the wrapped and the unwrapped case.
    """

    text = re.sub(r"\x1b\[[0-9;]*m", "", console.export_text())
    return re.sub(r"[\s│┃]+", "", text)


def test_a_stop_loss_is_never_ellipsized_at_the_redirected_width() -> None:
    # 140 is what a redirected run gets. The table carries the method's four-part answer as
    # well as the levels, so it needs the width; at 80 the prices fold instead (still whole,
    # just across lines — pinned by the next test).
    console = Console(width=NON_TERMINAL_WIDTH, record=True)

    _render_setups(_run(), console)

    rendered = _plain(console)
    assert "0.00267387" in rendered, "stop-loss lost digits"
    assert "0.00371227" in rendered, "take-profit lost digits"
    assert "…" not in rendered, "a value was ellipsized"


def test_no_number_is_ever_ellipsized_however_narrow_the_terminal() -> None:
    """At 40 columns a price folds across several lines, so a contiguity check cannot
    apply — but the guarantee still can: an ellipsis must never follow a digit. A clipped
    *symbol* is an annoyance; a clipped *level* is a wrong order."""

    console = Console(width=40, record=True)

    _render_setups(_run(), console)

    rendered = re.sub(r"\x1b\[[0-9;]*m", "", console.export_text())
    assert re.search(r"\d…", rendered) is None, rendered


def test_the_four_part_answer_has_its_own_columns() -> None:
    console = Console(width=NON_TERMINAL_WIDTH, record=True)

    _render_setups(_run(), console)

    text = re.sub(r"\x1b\[[0-9;]*m", "", console.export_text())
    # Trend, setup, entry status and decision are separate facts and separate columns.
    for header in ("Trend", "Setup", "Entry", "Decision"):
        assert header in text


def test_a_redirected_run_gets_a_width_the_table_fits_in() -> None:
    """The redirect path needs a real pipe: under pytest rich still reports a terminal,
    so asserting in-process would test nothing. A subprocess with stdout captured is the
    same condition as ``trader scan > scan.log``, which is where the 80-column fallback
    bit in the first place."""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from trader.cli import make_console; c = make_console();"
            " print(c.is_terminal, c.width)",
        ],
        capture_output=True,
        text=True,
        cwd="src",
        # An explicit environment with no ``COLUMNS``. The factory honours ``COLUMNS`` when
        # it is set — deliberately, since someone who exports it means it — so inheriting
        # the caller's environment would make this test pass or fail on whether the
        # developer happens to have a width exported. The case under test is the one where
        # nobody has said anything and the fallback has to be right on its own.
        env={key: value for key, value in os.environ.items() if key != "COLUMNS"},
    )

    assert result.returncode == 0, result.stderr
    # Only the width is asserted. Rich's own ``is_terminal`` may well be True here — with
    # FORCE_COLOR set it reports a terminal for a pipe — and that disagreement is exactly
    # the bug this guards: the width has to be right regardless of what rich believes.
    assert result.stdout.split()[1] == str(NON_TERMINAL_WIDTH), result.stdout
