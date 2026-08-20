"""Tests for `scan --dry-run-ai`: the zero-cost preview of the AI-analyst evidence.

Exercised end-to-end through ``run_scan`` with fake providers (no network), a mocked
sleep (no wall-clock throttling) and captured console output. No model client exists yet
in this slice, so "no request is made" is structural: the only collaborators the run has
are the two fakes below, and both record every call they receive.
"""

from __future__ import annotations

import math
import re

import pandas as pd
from rich.console import Console

from trader.candidate_gate import CooldownState
from trader.cli import run_scan, run_scan_forever
from trader.config import AIConfig, Config, StrategyConfig
from trader.market_data import Candles, OrderBook
from trader.provider import NEUTRAL, TimeframeResult


def _series(
    symbol: str,
    timeframe: str,
    closes: list[float],
    volume_spike: float,
    bar_offset: int = 0,
) -> Candles:
    rows = []
    for i, close in enumerate(closes):
        volume = 1_000.0 + i
        # Inflate only the final candle so relative volume (and therefore the volume
        # category, and therefore the total) differs per coin — otherwise every
        # identically-shaped coin would score identically and "top N by score" would be
        # a tie broken only by symbol.
        if i == len(closes) - 1:
            volume *= volume_spike
        rows.append([i + bar_offset, close, close + 1.0, close - 1.0, close, volume])
    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return Candles(symbol=symbol, timeframe=timeframe, frame=frame)


def _bullish(
    symbol: str, timeframe: str, rows: int, volume_spike: float, bar_offset: int = 0
) -> Candles:
    closes = []
    base = 100.0
    for i in range(rows):
        base += 1.0
        offset = {0: 0.0, 1: 3.0, 2: 5.0, 3: 3.0, 4: 0.0, 5: -2.0}[i % 6]
        closes.append(base + offset)
    return _series(symbol, timeframe, closes, volume_spike, bar_offset)


def _flat(
    symbol: str, timeframe: str, rows: int, volume_spike: float, bar_offset: int = 0
) -> Candles:
    closes = [100.0 + 20.0 * math.sin(2.0 * math.pi * i / 50.0) for i in range(rows)]
    return _series(symbol, timeframe, closes, volume_spike, bar_offset)


class FakeMarketData:
    """Serves synthetic candles and records every call, so unexpected I/O is visible."""

    def __init__(
        self,
        bullish: dict[str, float],
        *,
        rows: int = 260,
        short_history: frozenset[str] = frozenset(),
    ) -> None:
        self._bullish = bullish
        self._rows = rows
        self._short_history = short_history
        self.ohlcv_calls: list[tuple[str, str]] = []
        # Per-timeframe candle-clock offset. ``new_candle`` steps one timeframe forward,
        # which is what the cooldown keys on; timeframes left alone keep serving the same
        # bar, exactly as a real poll within a candle does.
        self._bar_offsets: dict[str, int] = {}

    def new_candle(self, timeframe: str) -> None:
        """Advance one timeframe's clock so its latest candle has a new open time."""
        self._bar_offsets[timeframe] = self._bar_offsets.get(timeframe, 0) + 1

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        self.ohlcv_calls.append((symbol, timeframe))
        rows = 60 if symbol in self._short_history else self._rows
        offset = self._bar_offsets.get(timeframe, 0)
        if symbol in self._bullish:
            return _bullish(symbol, timeframe, rows, self._bullish[symbol], offset)
        return _flat(symbol, timeframe, rows, 1.0, offset)

    def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        return OrderBook(
            symbol=symbol, best_bid=99.5, best_ask=100.5, bid_depth=500.0, ask_depth=400.0
        )


def _neutral(symbol: str, interval: str) -> TimeframeResult:
    return TimeframeResult(
        symbol=symbol,
        interval=interval,
        recommendation=NEUTRAL,
        buy=0,
        neutral=1,
        sell=0,
    )


class FakeAnalysisProvider:
    """Returns a neutral rating for everything and records every batch it serves."""

    def __init__(self) -> None:
        self.batch_calls: list[str] = []

    def get_analysis(
        self, symbol: str, exchange: str, screener: str, interval: str
    ) -> TimeframeResult:
        return _neutral(symbol, interval)

    def get_analysis_batch(
        self, symbols: list[str], exchange: str, screener: str, interval: str
    ) -> dict[str, TimeframeResult]:
        self.batch_calls.append(interval)
        return {symbol: _neutral(symbol, interval) for symbol in symbols}


def _config(
    watchlist: list[str],
    *,
    enabled: bool = True,
    min_score: float = 0.0,
    max_candidates: int = 5,
) -> Config:
    return Config(
        watchlist=watchlist,
        # The named-strategy checklist is switched off for these tests: their subject is the
        # scan pipeline — rendering, selection, cooldown, JSON — not the method. Leaving it on
        # would make every fixture also have to be a textbook pullback, and a fixture failing
        # the checklist would look like a plumbing failure. The catalogue has its own coverage
        # in test_strategy.py and test_setups_view.py.
        strategies=StrategyConfig(enabled=False),
        ai=AIConfig(
            enabled=enabled, min_score=min_score, max_candidates=max_candidates
        ),
    )


def _run(config: Config, market_data: FakeMarketData, *, dry_run_ai: bool = True) -> str:
    console = Console(width=200, record=True)
    run_scan(
        market_data,
        FakeAnalysisProvider(),
        console,
        config,
        sleep=lambda _seconds: None,
        dry_run_ai=dry_run_ai,
    )
    return console.export_text()


def _candidate_blocks(text: str) -> list[tuple[str, float | None]]:
    """Every rendered candidate header as ``(symbol, score)``, in printed order.

    The score clause is optional: the trend engine's 0-100 total was retired with the
    indicators behind it, so its headers carry a symbol and a direction only. The momentum
    scanner still prints one, and this parser serves both.
    """

    pattern = re.compile(
        r"\[\d+\] (\S+) — engine (?:LONG|SHORT)(?:, score ([\d.]+)/100)?"
    )
    return [
        (m.group(1), float(m.group(2)) if m.group(2) else None) for m in pattern.finditer(text)
    ]


def test_dry_run_prints_the_full_evidence_for_each_selected_candidate() -> None:
    market_data = FakeMarketData({"SOLUSDT": 3.0})

    text = _run(_config(["SOLUSDT"]), market_data)

    assert "AI analyst evidence (dry run — no request made)" in text
    # Symbol, engine direction and engine total.
    assert _candidate_blocks(text)[0][0] == "SOLUSDT"
    assert "engine LONG" in text
    # One summary row per configured timeframe, each carrying the trend shape, the
    # oscillator, the volatility, the relative volume and the structure.
    for timeframe in ("15m", "1h", "4h", "1d"):
        row = re.search(rf"^\s+{re.escape(timeframe)}\s+close=.*$", text, re.MULTILINE)
        assert row is not None, f"no evidence row for {timeframe}"
        assert "ema=" in row.group(0)
        assert "macd=" in row.group(0)
        assert "stoch=" in row.group(0)
        assert "atr=" in row.group(0)
        assert "rvol=" in row.group(0)
        assert "structure=" in row.group(0)
    # The engine's trade plan and the order-book liquidity.
    assert re.search(r"plan: entry=\S+\s+stop=\S+\s+target=\S+\s+rr=\S+", text)
    assert re.search(r"liquidity: spread=\S+%\s+bid_depth=\S+ USDT", text)


def test_dry_run_states_the_btc_regime_once_for_the_run() -> None:
    market_data = FakeMarketData({"SOLUSDT": 3.0, "ETHUSDT": 2.0})

    text = _run(_config(["SOLUSDT", "ETHUSDT"]), market_data)

    assert len(re.findall(r"BTC regime: (?:LONG|SHORT|NONE)", text)) == 1
def test_a_limited_history_coin_never_reaches_the_evidence() -> None:
    """It has no 200-period average, so no direction, so nothing to review.

    This replaces an assertion that the evidence carried a "history: LIMITED" caveat. The
    caveat still exists in the brief, but a limited-history coin can no longer become a
    candidate: the method's stack cannot be judged without its long-term filter.
    """
    limited = FakeMarketData({"SOLUSDT": 3.0}, short_history=frozenset({"SOLUSDT"}))
    full = FakeMarketData({"SOLUSDT": 3.0})

    assert "CANDIDATES (0)" in _run(_config(["SOLUSDT"]), limited)
    assert "CANDIDATES (1)" in _run(_config(["SOLUSDT"]), full)


def test_dry_run_says_so_plainly_when_the_stage_is_disabled() -> None:
    market_data = FakeMarketData({"SOLUSDT": 3.0})

    text = _run(_config(["SOLUSDT"], enabled=False), market_data)

    assert "AI analyst is disabled" in text
    assert _candidate_blocks(text) == []


def test_without_the_flag_no_evidence_is_printed_at_all() -> None:
    market_data = FakeMarketData({"SOLUSDT": 3.0})

    text = _run(_config(["SOLUSDT"]), market_data, dry_run_ai=False)

    assert "AI analyst" not in text
    assert _candidate_blocks(text) == []


# --- Cooldown across polls ------------------------------------------------------


def _poll(
    config: Config, market_data: FakeMarketData, cooldown: CooldownState
) -> list[tuple[str, float]]:
    """One scan sharing a cooldown, returning the candidates it selected."""
    console = Console(width=200, record=True)
    run_scan(
        market_data,
        FakeAnalysisProvider(),
        console,
        config,
        sleep=lambda _seconds: None,
        dry_run_ai=True,
        cooldown=cooldown,
    )
    return _candidate_blocks(console.export_text())


def test_a_second_poll_on_the_same_reference_candle_selects_nothing() -> None:
    market_data = FakeMarketData({"SOLUSDT": 3.0})
    config = _config(["SOLUSDT"])
    cooldown = CooldownState()

    first = _poll(config, market_data, cooldown)
    second = _poll(config, market_data, cooldown)

    assert [symbol for symbol, _score in first] == ["SOLUSDT"]
    assert second == []


def test_a_new_reference_candle_makes_the_coin_eligible_again() -> None:
    market_data = FakeMarketData({"SOLUSDT": 3.0})
    config = _config(["SOLUSDT"])
    cooldown = CooldownState()

    first = _poll(config, market_data, cooldown)
    market_data.new_candle(config.reference_timeframe)
    second = _poll(config, market_data, cooldown)

    assert [symbol for symbol, _score in first] == ["SOLUSDT"]
    assert [symbol for symbol, _score in second] == ["SOLUSDT"]


def test_only_the_configured_reference_timeframes_candle_re_opens_eligibility() -> None:
    market_data = FakeMarketData({"SOLUSDT": 3.0})
    config = _config(["SOLUSDT"])
    cooldown = CooldownState()

    _poll(config, market_data, cooldown)
    # A faster timeframe printing a new candle is not the reference timeframe (4h under
    # the default futures profile), so it must not re-open eligibility.
    market_data.new_candle("15m")

    assert _poll(config, market_data, cooldown) == []


def test_the_watch_loop_selects_once_per_reference_candle_not_once_per_poll() -> None:
    market_data = FakeMarketData({"SOLUSDT": 3.0})
    console = Console(width=200, record=True)

    iterations = run_scan_forever(
        market_data,
        FakeAnalysisProvider(),
        console,
        _config(["SOLUSDT"]),
        interval=300.0,
        dry_run_ai=True,
        sleep=lambda _seconds: None,
        max_iterations=3,
    )

    # Three polls, one selection: the loop owns a single cooldown across its iterations.
    assert iterations == 3
    assert [symbol for symbol, _score in _candidate_blocks(console.export_text())] == [
        "SOLUSDT"
    ]


def test_the_watch_loop_selects_nothing_when_the_stage_is_disabled() -> None:
    market_data = FakeMarketData({"SOLUSDT": 3.0})
    console = Console(width=200, record=True)

    run_scan_forever(
        market_data,
        FakeAnalysisProvider(),
        console,
        _config(["SOLUSDT"], enabled=False),
        interval=300.0,
        dry_run_ai=True,
        sleep=lambda _seconds: None,
        max_iterations=2,
    )

    text = console.export_text()
    assert _candidate_blocks(text) == []
    assert "MARKET CONTEXT" not in text
