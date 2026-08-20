"""Tests for the AI analyst on the momentum (`movers`) path.

The momentum scanner measures something different from the trend engine, so its evidence
is different — relative strength, breakout, volume, acceleration rather than the trend
engine's categories — but everything downstream (selection, review, validation, logging,
alerting) is the *same* code. These tests pin down both halves: that the evidence reflects
momentum, and that the shared path behaves identically here.

No network, no real credential, no wall-clock sleeps.
"""

from __future__ import annotations

import json
import re

import pandas as pd
import pytest
from rich.console import Console
from typer.testing import CliRunner

from trader.analyst import Action, AnalystError, AnalystReview, AnalystVerdict
from trader.brief import STRUCTURE_NOT_MEASURED, MarketBrief
from trader.candidate_gate import CooldownState, pair_movers
from trader.cli import app, build_movers_briefs, run_movers_cli
from trader.config import AIConfig, Config, TelegramConfig
from trader.decision_log import SCANNER_MOVERS, DecisionLog
from trader.market_data import Candles, OrderBook
from trader.notify import NotifyError

MOVERS_TF = "1d"


def _frame(rows: list[list[float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def _breakout(symbol: str, rows: int = 60, bar_offset: int = 0, spike: float = 3_000.0) -> Candles:
    """A range that breaks to a new high on heavy volume — a strong LONG mover."""
    data = []
    for i in range(rows - 1):
        close = 100.0 + (i % 3)
        data.append([i + bar_offset, close - 0.5, close + 1.0, close - 1.0, close, 1_000.0])
    data.append([rows - 1 + bar_offset, 103.0, 130.0, 102.0, 128.0, spike])
    return Candles(symbol=symbol, timeframe=MOVERS_TF, frame=_frame(data))


def _flat(symbol: str, rows: int = 60, bar_offset: int = 0) -> Candles:
    """A directionless range — scores far too low to surface."""
    data = [
        [i + bar_offset, 100.0, 100.5, 99.5, 100.0 + (i % 2) * 0.1, 1_000.0]
        for i in range(rows)
    ]
    return Candles(symbol=symbol, timeframe=MOVERS_TF, frame=_frame(data))


_LIQUID = OrderBook(
    symbol="x", best_bid=100.0, best_ask=100.2, bid_depth=1_000.0, ask_depth=1_000.0
)
_THIN = OrderBook(symbol="x", best_bid=100.0, best_ask=100.2, bid_depth=1.0, ask_depth=1.0)


class FakeMarketData:
    """Serves canned candles/books per symbol, with an advanceable candle clock."""

    def __init__(self, candles: dict[str, Candles], books: dict[str, OrderBook]) -> None:
        self._candles = candles
        self._books = books
        self._bar_offset = 0

    def new_candle(self) -> None:
        """Advance the daily clock so every symbol's latest candle has a new open time."""
        self._bar_offset += 1

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        base = self._candles[symbol]
        if self._bar_offset == 0:
            return base
        shifted = base.frame.copy()
        shifted["timestamp"] = shifted["timestamp"] + self._bar_offset
        return Candles(symbol=symbol, timeframe=timeframe, frame=shifted)

    def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        return self._books[symbol]


class FakeAnalyst:
    """Returns a scripted review (or raises), recording what it was asked."""

    def __init__(self, review: AnalystReview | Exception) -> None:
        self._review = review
        self.calls: list[tuple[str, ...]] = []

    def review(self, briefs, market: MarketBrief) -> AnalystReview:  # type: ignore[no-untyped-def]
        self.calls.append(tuple(b.symbol for b in briefs))
        if isinstance(self._review, Exception):
            raise self._review
        return self._review

    @property
    def call_count(self) -> int:
        return len(self.calls)


class FakeNotifier:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)
        if self._error is not None:
            raise self._error


def _verdict(symbol: str = "AAAUSDT", confidence: float = 85.0) -> AnalystVerdict:
    return AnalystVerdict(
        symbol=symbol,
        action=Action.LONG,
        confidence=confidence,
        entry=128.0,
        stop_loss=120.0,
        take_profits=(145.0,),
        rationale="Clean breakout on expanding volume, leading BTC.",
        key_risks=("Late entry near the high",),
        invalidation="Daily close back inside the range",
        agrees_with_engine=True,
    )


def _review(*verdicts: AnalystVerdict, warning: str | None = None) -> AnalystReview:
    return AnalystReview(
        verdicts=verdicts,
        concentration_warning=warning,
        model="gpt-5",
        prompt_tokens=1_800,
        completion_tokens=250,
        estimated_cost_usd=0.0061,
    )


def _config(
    watchlist: list[str],
    *,
    enabled: bool = True,
    min_score: float = 0.0,
    max_candidates: int = 5,
    alerts: bool = False,
    momentum_threshold: float = 40.0,
) -> Config:
    return Config(
        watchlist=watchlist,
        momentum_threshold=momentum_threshold,
        ai=AIConfig(enabled=enabled, min_score=min_score, max_candidates=max_candidates),
        telegram=TelegramConfig(enabled=alerts, min_confidence=70.0),
    )


def _market(symbols: dict[str, str]) -> FakeMarketData:
    """Build a fake market from ``{symbol: "breakout"|"flat"|"thin"}``."""
    candles: dict[str, Candles] = {"BTC/USDT": _flat("BTC/USDT")}
    books: dict[str, OrderBook] = {"BTC/USDT": _LIQUID}
    for symbol, kind in symbols.items():
        candles[symbol] = _flat(symbol) if kind == "flat" else _breakout(symbol)
        books[symbol] = _THIN if kind == "thin" else _LIQUID
    return FakeMarketData(candles, books)


def _run(
    config: Config,
    market_data: FakeMarketData,
    *,
    analyst: FakeAnalyst | None = None,
    dry_run_ai: bool = False,
    cooldown: CooldownState | None = None,
    decision_log: DecisionLog | None = None,
    notifier: FakeNotifier | None = None,
    json_path: str | None = None,
) -> str:
    console = Console(width=250, record=True)
    run_movers_cli(
        market_data,
        console,
        config,
        sleep=lambda _seconds: None,
        dry_run_ai=dry_run_ai,
        cooldown=cooldown,
        analyst=analyst,  # type: ignore[arg-type]
        decision_log=decision_log,
        notifier=notifier,  # type: ignore[arg-type]
        json_path=json_path,
    )
    return console.export_text()


def _candidate_blocks(text: str) -> list[str]:
    return re.findall(r"\[\d+\] (\S+) — engine (?:LONG|SHORT)", text)


# --- Selection ------------------------------------------------------------------


def test_only_surfaced_movers_are_reviewed() -> None:
    # AAAUSDT breaks out (surfaces); BBBUSDT is flat (below threshold); CCCUSDT is
    # illiquid. Only the first should ever cost a token.
    market = _market({"AAAUSDT": "breakout", "BBBUSDT": "flat", "CCCUSDT": "thin"})
    analyst = FakeAnalyst(_review(_verdict("AAAUSDT")))

    _run(_config(["AAAUSDT", "BBBUSDT", "CCCUSDT"]), market, analyst=analyst)

    assert analyst.call_count == 1
    assert set(analyst.calls[0]) == {"AAAUSDT"}


def test_pairing_skips_movers_that_did_not_surface() -> None:
    market = _market({"AAAUSDT": "breakout", "BBBUSDT": "flat"})
    console = Console(width=250, record=True)
    run = run_movers_cli(market, console, _config(["AAAUSDT", "BBBUSDT"]), sleep=lambda _s: None)

    paired = pair_movers(run)

    assert all(candidate.coin.surfaced for candidate in paired)
    assert {c.symbol for c in paired} == {"AAAUSDT"}


def test_the_score_floor_applies_to_the_momentum_score() -> None:
    market = _market({"AAAUSDT": "breakout"})
    console = Console(width=250, record=True)
    run = run_movers_cli(market, console, _config(["AAAUSDT"]), sleep=lambda _s: None)
    score = run.movers[0].score

    below = build_movers_briefs(run, _config(["AAAUSDT"], min_score=score - 1.0))
    above = build_movers_briefs(run, _config(["AAAUSDT"], min_score=score + 1.0))

    assert [b.symbol for b in below] == ["AAAUSDT"]
    assert above == ()


def test_the_per_run_cap_applies() -> None:
    market = _market({"AAAUSDT": "breakout", "BBBUSDT": "breakout", "CCCUSDT": "breakout"})
    console = Console(width=250, record=True)
    run = run_movers_cli(
        market, console, _config(["AAAUSDT", "BBBUSDT", "CCCUSDT"]), sleep=lambda _s: None
    )

    briefs = build_movers_briefs(run, _config(["AAAUSDT"], max_candidates=2))

    assert len(briefs) == 2


# --- Evidence -------------------------------------------------------------------


def test_the_evidence_reflects_what_momentum_measures() -> None:
    market = _market({"AAAUSDT": "breakout"})

    text = _run(_config(["AAAUSDT"]), market, dry_run_ai=True)

    assert "scanner: movers" in text
    assert "BTC benchmark return:" in text
    # The four momentum factors, not the trend engine's eight categories.
    assert "factors:" in text
    for factor in ("relative_strength", "breakout", "volume", "acceleration"):
        assert factor in text
    assert "categories:" not in text
    # The breakout level the stop is anchored to, the plan, and the liquidity.
    assert "breakout level:" in text
    assert re.search(r"plan: entry=\S+\s+stop=\S+\s+target=\S+", text)
    assert re.search(r"liquidity: spread=\S+%\s+bid_depth=\S+ USDT", text)


def test_the_evidence_carries_the_composite_momentum_score() -> None:
    market = _market({"AAAUSDT": "breakout"})
    console = Console(width=250, record=True)
    run = run_movers_cli(market, console, _config(["AAAUSDT"]), sleep=lambda _s: None)

    brief = build_movers_briefs(run, _config(["AAAUSDT"]))[0]

    assert brief.total == pytest.approx(run.movers[0].score)
    assert {c.name for c in brief.categories} == {
        "relative_strength",
        "breakout",
        "volume",
        "acceleration",
    }
    assert brief.breakout_level is not None
    # The momentum scanner measures no swing structure; saying "BROKEN" would tell the
    # model an absence was a finding.
    assert brief.timeframes[0].structure == STRUCTURE_NOT_MEASURED
    assert brief.timeframes[0].last_swing_high is None


def test_the_dry_run_makes_no_request() -> None:
    market = _market({"AAAUSDT": "breakout"})
    analyst = FakeAnalyst(_review(_verdict()))

    text = _run(_config(["AAAUSDT"]), market, analyst=analyst, dry_run_ai=True)

    assert analyst.call_count == 0
    assert _candidate_blocks(text) == ["AAAUSDT"]
    assert "no request made" in text


# --- Cooldown -------------------------------------------------------------------


def test_two_runs_on_the_same_daily_candle_review_once() -> None:
    market = _market({"AAAUSDT": "breakout"})
    cooldown = CooldownState()
    config = _config(["AAAUSDT"])

    first = _candidate_blocks(_run(config, market, dry_run_ai=True, cooldown=cooldown))
    second = _candidate_blocks(_run(config, market, dry_run_ai=True, cooldown=cooldown))

    assert first == ["AAAUSDT"]
    assert second == []


def test_a_new_daily_candle_re_opens_eligibility() -> None:
    market = _market({"AAAUSDT": "breakout"})
    cooldown = CooldownState()
    config = _config(["AAAUSDT"])

    _run(config, market, dry_run_ai=True, cooldown=cooldown)
    market.new_candle()
    second = _candidate_blocks(_run(config, market, dry_run_ai=True, cooldown=cooldown))

    assert second == ["AAAUSDT"]


# --- Verdicts, logging, alerting ------------------------------------------------


def test_verdicts_render_alongside_the_momentum_table() -> None:
    market = _market({"AAAUSDT": "breakout"})
    analyst = FakeAnalyst(_review(_verdict("AAAUSDT")))

    text = _run(_config(["AAAUSDT"]), market, analyst=analyst)

    assert "AI analyst verdicts" in text
    assert "Clean breakout on expanding volume" in text
    # The pre-existing momentum output is untouched.
    assert "Momentum movers" in text or "movers" in text.lower()
    assert "Advisory only" in text


def test_records_from_the_movers_path_are_distinguishable(tmp_path) -> None:
    log = DecisionLog(str(tmp_path / "decisions.jsonl"))
    market = _market({"AAAUSDT": "breakout"})
    analyst = FakeAnalyst(_review(_verdict("AAAUSDT")))

    _run(_config(["AAAUSDT"]), market, analyst=analyst, decision_log=log)

    records = [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["scanner"] == SCANNER_MOVERS
    assert records[0]["symbol"] == "AAAUSDT"


def test_both_scanners_can_share_one_decision_log(tmp_path) -> None:
    from test_cli_ai_review import FakeAnalyst as ScanAnalyst
    from test_cli_ai_review import _config as scan_config
    from test_cli_ai_review import _review as scan_review
    from test_cli_ai_review import _run as scan_run
    from test_cli_ai_review import _verdict as scan_verdict

    log = DecisionLog(str(tmp_path / "decisions.jsonl"))

    scan_run(scan_config(["SOLUSDT"]), ScanAnalyst(scan_review(scan_verdict())), decision_log=log)
    _run(
        _config(["AAAUSDT"]),
        _market({"AAAUSDT": "breakout"}),
        analyst=FakeAnalyst(_review(_verdict("AAAUSDT"))),
        decision_log=log,
    )

    records = [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]
    assert [r["scanner"] for r in records] == ["scan", "movers"]


def test_an_alert_from_the_movers_path_names_the_scanner() -> None:
    notifier = FakeNotifier()
    market = _market({"AAAUSDT": "breakout"})
    analyst = FakeAnalyst(_review(_verdict("AAAUSDT", confidence=90.0)))

    _run(_config(["AAAUSDT"], alerts=True), market, analyst=analyst, notifier=notifier)

    assert len(notifier.sent) == 1
    assert "Engine (movers)" in notifier.sent[0]
    assert "no order has been placed" in notifier.sent[0]


def test_an_analyst_outage_leaves_the_momentum_output_intact(tmp_path) -> None:
    log = DecisionLog(str(tmp_path / "decisions.jsonl"))
    market = _market({"AAAUSDT": "breakout"})
    analyst = FakeAnalyst(AnalystError("boom after 3 attempts"))

    text = _run(_config(["AAAUSDT"]), market, analyst=analyst, decision_log=log)

    assert "AI analyst unavailable" in text
    assert "AAAUSDT" in text
    records = [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["status"] == "failed"
    assert records[0]["scanner"] == SCANNER_MOVERS


def test_a_delivery_failure_does_not_abort_a_movers_run() -> None:
    market = _market({"AAAUSDT": "breakout"})
    analyst = FakeAnalyst(_review(_verdict("AAAUSDT", confidence=90.0)))
    notifier = FakeNotifier(NotifyError("could not reach Telegram"))

    text = _run(_config(["AAAUSDT"], alerts=True), market, analyst=analyst, notifier=notifier)

    assert "Could not alert for AAAUSDT" in text
    assert "AI analyst verdicts" in text


def test_the_json_report_gains_a_review_section(tmp_path) -> None:
    out = tmp_path / "movers.json"
    market = _market({"AAAUSDT": "breakout"})
    analyst = FakeAnalyst(_review(_verdict("AAAUSDT"), warning="single-name concentration"))

    _run(_config(["AAAUSDT"]), market, analyst=analyst, json_path=str(out))

    payload = json.loads(out.read_text(encoding="utf-8"))
    # The pre-existing report is unchanged and additive.
    assert "coins" in payload and "movers" in payload and "disclaimer" in payload
    assert payload["review"]["verdicts"][0]["symbol"] == "AAAUSDT"
    assert payload["review"]["concentration_warning"] == "single-name concentration"


def test_without_the_analyst_the_movers_output_is_unchanged(tmp_path) -> None:
    out = tmp_path / "movers.json"
    market = _market({"AAAUSDT": "breakout"})

    text = _run(_config(["AAAUSDT"], enabled=False), market, json_path=str(out))

    assert "AI analyst" not in text
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "review" not in payload


# --- Command surface ------------------------------------------------------------


def _write_config(tmp_path, body: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(f"watchlist: [AAAUSDT]\n{body}", encoding="utf-8")
    return str(path)


def _no_env_file(tmp_path) -> list[str]:
    """`--env-file` args pointing at a path that does not exist.

    Stated explicitly so a real `.env` in the working directory cannot make a
    "missing credential" test pass on one machine and fail on another.
    """

    return ["--env-file", str(tmp_path / "absent.env")]


def test_the_movers_help_documents_both_ai_switches() -> None:
    result = CliRunner().invoke(app, ["movers", "--help"])

    assert "--ai" in result.output
    assert "--no-ai" in result.output
    assert "--dry-run-ai" in result.output


def test_enabling_the_analyst_from_the_command_line_fails_fast_without_a_key(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config_path = _write_config(tmp_path, "ai:\n  enabled: false\n")

    result = CliRunner().invoke(
        app, ["movers", "--config", config_path, "--ai", *_no_env_file(tmp_path)]
    )

    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output


def test_the_movers_credential_check_precedes_any_market_data(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    built: list[str] = []

    class ExplodingProvider:
        def __init__(self, *args: object, **kwargs: object) -> None:
            built.append("constructed")

    monkeypatch.setattr("trader.cli.CcxtBinanceProvider", ExplodingProvider)
    config_path = _write_config(tmp_path, "ai:\n  enabled: true\n")

    result = CliRunner().invoke(
        app, ["movers", "--config", config_path, *_no_env_file(tmp_path)]
    )

    assert result.exit_code == 2
    assert built == []
