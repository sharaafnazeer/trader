"""Tests for the AI-analyst review stage inside `scan`.

The analyst is injected as a fake, so nothing here contacts a model or reads a real
credential. What these assert is the *application layer's* behaviour: that the stage runs
only when it should, that verdicts are rendered with their levels and conflicts, that a
bad verdict is never shown as a plan, and — most importantly — that no failure of the
analyst can cost the trader the scan they already paid to compute.
"""

from __future__ import annotations

import json
import os

import pytest
from rich.console import Console
from typer.testing import CliRunner

# The synthetic market fixtures are shared with the dry-run tests rather than duplicated;
# pytest puts the tests directory on the import path.
from test_cli_dry_run_ai import FakeAnalysisProvider, FakeMarketData
from trader.analyst import Action, AnalystError, AnalystReview, AnalystVerdict
from trader.brief import MarketBrief
from trader.cli import app, run_scan, run_scan_forever
from trader.config import (
    AIConfig,
    Config,
    ConfigError,
    StrategyConfig,
    TelegramConfig,
    resolve_ai_credential,
    resolve_telegram_credentials,
)
from trader.decision_log import DecisionLog
from trader.direction import Direction
from trader.notify import NotifyError


class FakeAnalyst:
    """Returns a scripted review (or raises), recording exactly what it was asked."""

    def __init__(self, review: AnalystReview | Exception) -> None:
        self._review = review
        self.calls: list[tuple[tuple[str, ...], Direction]] = []

    def review(self, briefs, market: MarketBrief) -> AnalystReview:  # type: ignore[no-untyped-def]
        self.calls.append((tuple(b.symbol for b in briefs), market.btc_direction))
        if isinstance(self._review, Exception):
            raise self._review
        return self._review

    @property
    def call_count(self) -> int:
        return len(self.calls)


def _verdict(
    symbol: str = "SOLUSDT",
    action: Action = Action.LONG,
    *,
    confidence: float = 74.0,
    entry: float | None = 100.0,
    stop_loss: float | None = 94.0,
    take_profits: tuple[float, ...] = (112.0, 125.0),
    rationale: str = "Higher-timeframe trend intact with expanding volume.",
    invalidation: str | None = "4h close below 94",
    key_risks: tuple[str, ...] = ("BTC losing its range low",),
    agrees: bool = True,
) -> AnalystVerdict:
    return AnalystVerdict(
        symbol=symbol,
        action=action,
        confidence=confidence,
        entry=entry,
        stop_loss=stop_loss,
        take_profits=take_profits,
        rationale=rationale,
        key_risks=key_risks,
        invalidation=invalidation,
        agrees_with_engine=agrees,
    )


def _review(
    *verdicts: AnalystVerdict, warning: str | None = None, **kwargs: object
) -> AnalystReview:
    defaults: dict[str, object] = {
        "verdicts": verdicts,
        "concentration_warning": warning,
        "model": "gpt-5",
        "prompt_tokens": 2_400,
        "completion_tokens": 350,
        "estimated_cost_usd": 0.0087,
    }
    defaults.update(kwargs)
    return AnalystReview(**defaults)  # type: ignore[arg-type]


def _config(
    watchlist: list[str],
    *,
    enabled: bool = True,
    min_score: float = 0.0,
    max_candidates: int = 5,
    alerts: bool = False,
    min_confidence: float = 70.0,
) -> Config:
    return Config(
        watchlist=watchlist,
        # The named-strategy checklist is switched off for these tests: their subject is the
        # scan pipeline — rendering, selection, cooldown, JSON — not the method. Leaving it on
        # would make every fixture also have to be a textbook pullback, and a fixture failing
        # the checklist would look like a plumbing failure. The catalogue has its own coverage
        # in test_strategy.py and test_setups_view.py.
        strategies=StrategyConfig(enabled=False),
        ai=AIConfig(enabled=enabled, min_score=min_score, max_candidates=max_candidates),
        telegram=TelegramConfig(enabled=alerts, min_confidence=min_confidence),
    )


def _run(
    config: Config,
    analyst: FakeAnalyst | None,
    *,
    market_data: FakeMarketData | None = None,
    json_path: str | None = None,
    decision_log: DecisionLog | None = None,
    notifier: object | None = None,
) -> str:
    console = Console(width=250, record=True)
    run_scan(
        market_data or FakeMarketData({"SOLUSDT": 4.0}),
        FakeAnalysisProvider(),
        console,
        config,
        sleep=lambda _seconds: None,
        analyst=analyst,
        json_path=json_path,
        decision_log=decision_log,
        notifier=notifier,  # type: ignore[arg-type]
    )
    return console.export_text()


class FakeNotifier:
    """Records every delivered message, or raises a scripted failure."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)
        if self._error is not None:
            raise self._error


def test_one_review_request_carries_every_selected_candidate() -> None:
    analyst = FakeAnalyst(_review(_verdict("SOLUSDT"), _verdict("ETHUSDT")))
    market_data = FakeMarketData({"SOLUSDT": 4.0, "ETHUSDT": 2.0, "ADAUSDT": 1.2})

    _run(_config(["SOLUSDT", "ETHUSDT", "ADAUSDT"]), analyst, market_data=market_data)

    assert analyst.call_count == 1
    reviewed, _btc = analyst.calls[0]
    assert set(reviewed) == {"SOLUSDT", "ETHUSDT", "ADAUSDT"}


def test_nothing_selected_makes_no_request() -> None:
    """No candidates means no paid request.

    The lever used to be an unreachable score floor. The trend engine has no score to
    floor any more, so the honest way to select nothing is a coin that resolves no
    direction — which is also the case that actually happens on a quiet watchlist.
    """

    analyst = FakeAnalyst(_review())

    _run(_config(["FLATUSDT"]), analyst, market_data=FakeMarketData({}))

    assert analyst.call_count == 0


def test_the_analyst_is_never_called_when_the_stage_is_disabled() -> None:
    analyst = FakeAnalyst(_review(_verdict()))

    text = _run(_config(["SOLUSDT"], enabled=False), analyst)

    assert analyst.call_count == 0
    assert "AI analyst verdicts" not in text


def test_a_verdict_is_rendered_with_its_action_confidence_levels_and_rationale() -> None:
    analyst = FakeAnalyst(_review(_verdict()))

    text = _run(_config(["SOLUSDT"]), analyst)

    assert "AI analyst verdicts" in text
    assert "LONG" in text
    assert "74" in text
    assert "100" in text and "94" in text
    assert "112" in text and "125" in text
    assert "4h close below 94" in text
    assert "Higher-timeframe trend intact" in text
    assert "BTC losing its range low" in text


def test_a_wait_verdict_shows_its_reason_and_no_levels() -> None:
    analyst = FakeAnalyst(
        _review(
            _verdict(
                action=Action.WAIT,
                entry=None,
                stop_loss=None,
                take_profits=(),
                invalidation=None,
                key_risks=(),
                rationale="Needs a 4h close above the range high first.",
            )
        )
    )

    text = _run(_config(["SOLUSDT"]), analyst)

    assert "WAIT" in text
    assert "Needs a 4h close above the range high first." in text
    assert "112" not in text


def test_a_disagreement_with_the_engine_is_visibly_marked() -> None:
    agreeing = FakeAnalyst(_review(_verdict(agrees=True)))
    disagreeing = FakeAnalyst(
        _review(
            _verdict(
                action=Action.SHORT,
                entry=100.0,
                stop_loss=106.0,
                take_profits=(88.0,),
                invalidation="4h close above 106",
                agrees=False,
            )
        )
    )

    assert "DISAGREES" not in _run(_config(["SOLUSDT"]), agreeing)
    assert "DISAGREES" in _run(_config(["SOLUSDT"]), disagreeing)


def test_a_concentration_warning_is_displayed() -> None:
    analyst = FakeAnalyst(
        _review(_verdict(), warning="All 3 candidates are shorts on correlated L1s.")
    )

    text = _run(_config(["SOLUSDT"]), analyst)

    assert "Concentration warning" in text
    assert "correlated L1s" in text


def test_an_invalid_verdict_is_rejected_with_a_reason_and_never_shown_as_a_plan() -> None:
    # A long whose stop sits above its entry: confident, well-written, impossible.
    analyst = FakeAnalyst(_review(_verdict(stop_loss=150.0, take_profits=(90.0,))))

    text = _run(_config(["SOLUSDT"]), analyst)

    assert "Rejected verdicts (not tradeable)" in text
    assert "not below entry" in text
    # The impossible levels never appear as a tradeable row.
    assert "150" not in text.split("Rejected verdicts")[0]


def test_an_analyst_outage_leaves_the_results_table_intact() -> None:
    analyst = FakeAnalyst(AnalystError("no usable review after 3 attempt(s): boom"))

    text = _run(_config(["SOLUSDT"]), analyst)

    assert "AI analyst unavailable" in text
    # The engine's own work — the thing already paid for — is still rendered in full.
    assert "Setups" in text
    assert "SOLUSDT" in text


def test_an_analyst_outage_does_not_raise() -> None:
    analyst = FakeAnalyst(AnalystError("down"))

    # No exception escapes run_scan, so the command exits zero.
    _run(_config(["SOLUSDT"]), analyst)


def test_token_usage_and_estimated_cost_are_reported() -> None:
    analyst = FakeAnalyst(_review(_verdict()))

    text = _run(_config(["SOLUSDT"]), analyst)

    assert "2400 prompt" in text
    assert "350 completion" in text
    assert "$0.0087" in text


def test_unpriced_tokens_report_unknown_cost_rather_than_zero() -> None:
    analyst = FakeAnalyst(_review(_verdict(), estimated_cost_usd=None))

    text = _run(_config(["SOLUSDT"]), analyst)

    assert "not configured" in text
    assert "$0.00" not in text


def test_every_render_carries_the_advisory_only_disclaimer() -> None:
    analyst = FakeAnalyst(_review(_verdict()))

    text = _run(_config(["SOLUSDT"]), analyst)

    assert "Advisory only" in text
    assert "No order has been placed" in text


def test_the_btc_regime_is_passed_to_the_analyst() -> None:
    analyst = FakeAnalyst(_review(_verdict()))

    _run(_config(["SOLUSDT"]), analyst)

    _symbols, btc_direction = analyst.calls[0]
    assert isinstance(btc_direction, Direction)


def test_the_json_review_section_matches_the_rendered_verdicts(tmp_path) -> None:
    analyst = FakeAnalyst(
        _review(_verdict(), _verdict("ETHUSDT", stop_loss=150.0), warning="too many longs")
    )
    out = tmp_path / "run.json"
    market_data = FakeMarketData({"SOLUSDT": 4.0, "ETHUSDT": 2.0})

    text = _run(
        _config(["SOLUSDT", "ETHUSDT"]), analyst, market_data=market_data, json_path=str(out)
    )

    payload = json.loads(out.read_text(encoding="utf-8"))
    review = payload["review"]
    assert [v["symbol"] for v in review["verdicts"]] == ["SOLUSDT"]
    assert [r["verdict"]["symbol"] for r in review["rejected"]] == ["ETHUSDT"]
    assert review["concentration_warning"] == "too many longs"
    assert review["model"] == "gpt-5"
    assert review["prompt_tokens"] == 2_400
    # The same split the table showed.
    assert "Rejected verdicts" in text
    assert review["verdicts"][0]["action"] == "long"


def test_no_review_section_is_written_when_the_stage_is_disabled(tmp_path) -> None:
    out = tmp_path / "run.json"

    _run(_config(["SOLUSDT"], enabled=False), None, json_path=str(out))

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "review" not in payload
    assert "coins" in payload


# --- Decision log -----------------------------------------------------------------


def _records(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_run_writes_one_record_per_reviewed_candidate(tmp_path) -> None:
    log = DecisionLog(str(tmp_path / "decisions.jsonl"))
    analyst = FakeAnalyst(_review(_verdict("SOLUSDT"), _verdict("ETHUSDT")))
    market_data = FakeMarketData({"SOLUSDT": 4.0, "ETHUSDT": 2.0})

    _run(_config(["SOLUSDT", "ETHUSDT"]), analyst, market_data=market_data, decision_log=log)

    records = _records(log.path)
    assert len(records) == 2
    assert {r["symbol"] for r in records} == {"SOLUSDT", "ETHUSDT"}
    assert all(r["status"] == "accepted" for r in records)
    assert all(r["scanner"] == "scan" for r in records)


def test_a_rejected_verdict_is_recorded_from_a_real_run(tmp_path) -> None:
    log = DecisionLog(str(tmp_path / "decisions.jsonl"))
    analyst = FakeAnalyst(_review(_verdict(stop_loss=150.0, take_profits=(90.0,))))

    _run(_config(["SOLUSDT"]), analyst, decision_log=log)

    record = _records(log.path)[0]
    assert record["status"] == "rejected"
    assert "not below entry" in record["reason"]


def test_an_analyst_outage_still_records_every_selected_candidate(tmp_path) -> None:
    log = DecisionLog(str(tmp_path / "decisions.jsonl"))
    analyst = FakeAnalyst(AnalystError("no usable review after 3 attempt(s): boom"))
    market_data = FakeMarketData({"SOLUSDT": 4.0, "ETHUSDT": 2.0})

    _run(_config(["SOLUSDT", "ETHUSDT"]), analyst, market_data=market_data, decision_log=log)

    records = _records(log.path)
    assert len(records) == 2
    assert all(r["status"] == "failed" for r in records)
    assert all("boom" in r["reason"] for r in records)
    # The engine's own view is preserved, so the setup is not lost to the measurement.
    # The trend engine records no total since its score was retired; the field is present
    # and explicitly null rather than absent, so a reader can tell "no score" from "lost".
    assert all(r["engine_total"] is None for r in records)


def test_nothing_is_recorded_when_the_stage_is_disabled(tmp_path) -> None:
    log = DecisionLog(str(tmp_path / "decisions.jsonl"))
    analyst = FakeAnalyst(_review(_verdict()))

    _run(_config(["SOLUSDT"], enabled=False), analyst, decision_log=log)

    assert not log.path.exists()


def test_nothing_is_recorded_when_no_candidate_is_selected(tmp_path) -> None:
    log = DecisionLog(str(tmp_path / "decisions.jsonl"))
    analyst = FakeAnalyst(_review())

    _run(_config(["FLATUSDT"]), analyst, market_data=FakeMarketData({}), decision_log=log)

    assert not log.path.exists()


def test_successive_runs_accumulate_in_one_file(tmp_path) -> None:
    log = DecisionLog(str(tmp_path / "decisions.jsonl"))

    _run(_config(["SOLUSDT"]), FakeAnalyst(_review(_verdict())), decision_log=log)
    _run(_config(["SOLUSDT"]), FakeAnalyst(_review(_verdict())), decision_log=log)

    assert len(_records(log.path)) == 2


def test_a_log_write_failure_does_not_cost_the_run(tmp_path) -> None:
    # Point the log at a path that cannot be created: a directory where a file must go.
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    log = DecisionLog(str(blocked))
    analyst = FakeAnalyst(_review(_verdict()))

    text = _run(_config(["SOLUSDT"]), analyst, decision_log=log)

    assert "Could not write the decision log" in text
    # The verdicts and the engine's table both still rendered.
    assert "AI analyst verdicts" in text
    assert "Setups" in text


# --- Alerting -----------------------------------------------------------------------


def test_a_confident_actionable_verdict_sends_exactly_one_alert() -> None:
    notifier = FakeNotifier()
    analyst = FakeAnalyst(_review(_verdict(confidence=85.0)))

    _run(_config(["SOLUSDT"], alerts=True, min_confidence=70.0), analyst, notifier=notifier)

    assert len(notifier.sent) == 1
    assert "SOLUSDT" in notifier.sent[0]


def test_a_verdict_below_the_confidence_floor_sends_nothing() -> None:
    notifier = FakeNotifier()
    analyst = FakeAnalyst(_review(_verdict(confidence=55.0)))

    _run(_config(["SOLUSDT"], alerts=True, min_confidence=70.0), analyst, notifier=notifier)

    assert notifier.sent == []


@pytest.mark.parametrize("action", [Action.WAIT, Action.AVOID])
def test_a_high_confidence_stand_aside_verdict_sends_nothing(action: Action) -> None:
    notifier = FakeNotifier()
    analyst = FakeAnalyst(
        _review(
            _verdict(
                action=action,
                confidence=99.0,
                entry=None,
                stop_loss=None,
                take_profits=(),
                invalidation=None,
                rationale="Confident that the right move is to stand aside.",
            )
        )
    )

    _run(_config(["SOLUSDT"], alerts=True, min_confidence=70.0), analyst, notifier=notifier)

    assert notifier.sent == []


def test_the_alert_contains_everything_needed_to_act() -> None:
    notifier = FakeNotifier()
    analyst = FakeAnalyst(
        _review(_verdict(confidence=85.0), warning="2 of 3 are correlated L1 longs")
    )

    _run(_config(["SOLUSDT"], alerts=True), analyst, notifier=notifier)

    message = notifier.sent[0]
    assert "SOLUSDT" in message
    assert "LONG" in message
    assert "85" in message
    assert "100" in message and "94" in message
    assert "112 / 125" in message
    assert "Higher-timeframe trend intact" in message
    assert "2 of 3 are correlated L1 longs" in message
    assert "no order has been placed" in message


def test_alerting_disabled_still_renders_and_records(tmp_path) -> None:
    log = DecisionLog(str(tmp_path / "decisions.jsonl"))
    notifier = FakeNotifier()
    analyst = FakeAnalyst(_review(_verdict(confidence=95.0)))

    text = _run(
        _config(["SOLUSDT"], alerts=False),
        analyst,
        notifier=None,
        decision_log=log,
    )

    assert notifier.sent == []
    assert "AI analyst verdicts" in text
    assert len(_records(log.path)) == 1


def test_a_delivery_failure_is_reported_and_costs_nothing_else(tmp_path) -> None:
    log = DecisionLog(str(tmp_path / "decisions.jsonl"))
    notifier = FakeNotifier(NotifyError("could not reach Telegram: timed out"))
    analyst = FakeAnalyst(_review(_verdict(confidence=90.0)))

    text = _run(
        _config(["SOLUSDT"], alerts=True), analyst, notifier=notifier, decision_log=log
    )

    assert "Could not alert for SOLUSDT" in text
    # Everything else survived: the engine's table, the verdicts, and the log record.
    assert "Setups" in text
    assert "AI analyst verdicts" in text
    assert len(_records(log.path)) == 1


def test_one_failing_send_does_not_swallow_the_others() -> None:
    class FlakyNotifier:
        def __init__(self) -> None:
            self.sent: list[str] = []

        def send(self, text: str) -> None:
            self.sent.append(text)
            if "SOLUSDT" in text:
                raise NotifyError("transient")

    notifier = FlakyNotifier()
    analyst = FakeAnalyst(
        _review(_verdict("SOLUSDT", confidence=90.0), _verdict("ETHUSDT", confidence=90.0))
    )
    market_data = FakeMarketData({"SOLUSDT": 4.0, "ETHUSDT": 2.0})

    text = _run(
        _config(["SOLUSDT", "ETHUSDT"], alerts=True),
        analyst,
        market_data=market_data,
        notifier=notifier,
    )

    # Both were attempted; only the healthy one counts as sent.
    assert len(notifier.sent) == 2
    assert "Could not alert for SOLUSDT" in text
    assert "Sent 1 alert(s)." in text


def test_a_rejected_verdict_is_never_alerted() -> None:
    notifier = FakeNotifier()
    # Impossible geometry: rejected before it could ever reach a phone.
    analyst = FakeAnalyst(_review(_verdict(confidence=99.0, stop_loss=150.0, take_profits=(90.0,))))

    _run(_config(["SOLUSDT"], alerts=True), analyst, notifier=notifier)

    assert notifier.sent == []


def test_a_persistent_setup_alerts_once_per_reference_candle() -> None:
    notifier = FakeNotifier()
    market_data = FakeMarketData({"SOLUSDT": 4.0})
    console = Console(width=250, record=True)
    config = _config(["SOLUSDT"], alerts=True)

    run_scan_forever(
        market_data,
        FakeAnalysisProvider(),
        console,
        config,
        interval=300.0,
        analyst=FakeAnalyst(_review(_verdict(confidence=90.0))),
        notifier=notifier,
        sleep=lambda _seconds: None,
        max_iterations=3,
    )

    # Three polls within one 4h candle: the cooldown means one selection, so one alert.
    assert len(notifier.sent) == 1

    # A new reference candle re-opens eligibility, and a second alert follows.
    market_data.new_candle(config.reference_timeframe)
    run_scan(
        market_data,
        FakeAnalysisProvider(),
        console,
        config,
        sleep=lambda _seconds: None,
        analyst=FakeAnalyst(_review(_verdict(confidence=90.0))),
        notifier=notifier,
    )
    assert len(notifier.sent) == 2


# --- Credentials ----------------------------------------------------------------


def test_a_missing_credential_is_reported_by_variable_name() -> None:
    config = AIConfig(enabled=True, api_key_env="OPENAI_API_KEY")

    with pytest.raises(ConfigError) as excinfo:
        resolve_ai_credential(config, getenv=lambda _name: None)

    assert "OPENAI_API_KEY" in str(excinfo.value)


def test_a_blank_credential_counts_as_missing() -> None:
    config = AIConfig(enabled=True, api_key_env="MY_KEY")

    with pytest.raises(ConfigError, match="MY_KEY"):
        resolve_ai_credential(config, getenv=lambda _name: "   ")


def test_a_present_credential_is_returned() -> None:
    config = AIConfig(enabled=True, api_key_env="MY_KEY")

    assert resolve_ai_credential(config, getenv=lambda _name: "sk-test") == "sk-test"


def test_the_credential_is_read_from_the_configured_variable() -> None:
    config = AIConfig(enabled=True, api_key_env="CUSTOM_VAR")
    seen: list[str] = []

    def getenv(name: str) -> str | None:
        seen.append(name)
        return "sk-test"

    resolve_ai_credential(config, getenv=getenv)

    assert seen == ["CUSTOM_VAR"]


def test_missing_telegram_credentials_are_reported_by_name() -> None:
    config = TelegramConfig(enabled=True)

    with pytest.raises(ConfigError) as excinfo:
        resolve_telegram_credentials(config, getenv=lambda _name: None)

    message = str(excinfo.value)
    assert "TELEGRAM_BOT_TOKEN" in message
    assert "TELEGRAM_CHAT_ID" in message


def test_a_single_missing_telegram_variable_is_named_alone() -> None:
    config = TelegramConfig(enabled=True)

    with pytest.raises(ConfigError) as excinfo:
        resolve_telegram_credentials(
            config, getenv=lambda name: "x" if name == "TELEGRAM_BOT_TOKEN" else None
        )

    message = str(excinfo.value)
    assert "TELEGRAM_CHAT_ID" in message
    assert "TELEGRAM_BOT_TOKEN" not in message


def test_present_telegram_credentials_are_returned() -> None:
    config = TelegramConfig(enabled=True, bot_token_env="BT", chat_id_env="CI")

    token, chat_id = resolve_telegram_credentials(
        config, getenv=lambda name: {"BT": " tok ", "CI": " 42 "}[name]
    )

    assert (token, chat_id) == ("tok", "42")


def test_enabling_alerts_without_credentials_stops_the_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    config_path = _write_config(
        tmp_path, "ai:\n  enabled: true\ntelegram:\n  enabled: true\n"
    )

    result = CliRunner().invoke(
        app, ["scan", "--config", config_path, *_no_env_file(tmp_path)]
    )

    assert result.exit_code == 2
    assert "TELEGRAM_BOT_TOKEN" in result.output


def test_a_credential_from_an_env_file_satisfies_the_check(tmp_path, monkeypatch) -> None:
    # This is the case a scheduled run hits: no login shell, so nothing is exported, but
    # a file in the working directory is still there.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-from-file\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "watchlist: [SOLUSDT]\nai:\n  enabled: true\n", encoding="utf-8"
    )

    calls: list[str] = []

    class RecordingProvider:
        def __init__(self, *args: object, **kwargs: object) -> None:
            calls.append("constructed")

        def get_ohlcv(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("stop before any real fetch")

        def get_order_book(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("stop before any real fetch")

    monkeypatch.setattr("trader.cli.CcxtBinanceProvider", RecordingProvider)

    result = CliRunner().invoke(app, ["scan", "--config", "config.yaml"])

    # No credential error, and the run got past start-up into provider construction.
    assert "OPENAI_API_KEY is not set" not in result.output
    assert calls == ["constructed"]


def test_the_env_file_does_not_override_an_exported_credential(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-shell")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-from-file\n", encoding="utf-8")

    from trader.config import load_env_file

    load_env_file(".env")

    assert os.environ["OPENAI_API_KEY"] == "sk-from-shell"


def test_loading_an_env_file_never_prints_the_value(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-super-secret\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "watchlist: [SOLUSDT]\nai:\n  enabled: true\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "trader.cli.CcxtBinanceProvider",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")),
    )

    result = CliRunner().invoke(app, ["scan", "--config", "config.yaml"])

    assert "sk-super-secret" not in result.output
    # It says *what* it loaded, by name only.
    assert "OPENAI_API_KEY" in result.output


def test_a_missing_env_file_changes_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "watchlist: [SOLUSDT]\nai:\n  enabled: true\n", encoding="utf-8"
    )

    result = CliRunner().invoke(app, ["scan", "--config", "config.yaml"])

    # No .env, nothing exported: the same clear failure as before this feature existed.
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output


def test_a_sentinel_credential_never_reaches_the_rendered_output() -> None:
    analyst = FakeAnalyst(_review(_verdict()))

    text = _run(_config(["SOLUSDT"]), analyst)

    assert "sk-" not in text


# --- Command-level override and start-up check ----------------------------------


def _write_config(tmp_path, body: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(f"watchlist: [SOLUSDT]\n{body}", encoding="utf-8")
    return str(path)


def _no_env_file(tmp_path) -> list[str]:
    """`--env-file` args pointing at a path that does not exist.

    Every "missing credential" test must say this explicitly. Without it the CLI would
    load whatever `.env` happens to sit in the working directory — so the test would pass
    on CI and fail on the machine of anyone who actually configured the feature, which is
    the worst possible way for a test to be wrong.
    """

    return ["--env-file", str(tmp_path / "absent.env")]


def test_enabling_from_the_command_line_fails_fast_when_the_credential_is_missing(
    tmp_path, monkeypatch
) -> None:
    # The config disables the analyst, so a check performed at load time would never run.
    # Passing --ai must still trigger it — this is the exact hole the ordering guards.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config_path = _write_config(tmp_path, "ai:\n  enabled: false\n")

    result = CliRunner().invoke(
        app, ["scan", "--config", config_path, "--ai", *_no_env_file(tmp_path)]
    )

    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output


def test_the_check_names_the_configured_variable(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MY_TRADER_KEY", raising=False)
    config_path = _write_config(
        tmp_path, "ai:\n  enabled: true\n  api_key_env: MY_TRADER_KEY\n"
    )

    result = CliRunner().invoke(
        app, ["scan", "--config", config_path, *_no_env_file(tmp_path)]
    )

    assert result.exit_code == 2
    assert "MY_TRADER_KEY" in result.output


def test_the_credential_check_happens_before_any_market_data_is_fetched(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fetched: list[str] = []

    class ExplodingProvider:
        def __init__(self, *args: object, **kwargs: object) -> None:
            fetched.append("constructed")

    monkeypatch.setattr("trader.cli.CcxtBinanceProvider", ExplodingProvider)
    config_path = _write_config(tmp_path, "ai:\n  enabled: true\n")

    result = CliRunner().invoke(
        app, ["scan", "--config", config_path, *_no_env_file(tmp_path)]
    )

    assert result.exit_code == 2
    # The run stopped before it even built a market-data provider, let alone fetched.
    assert fetched == []


def test_disabling_from_the_command_line_skips_the_check_entirely(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config_path = _write_config(tmp_path, "ai:\n  enabled: true\n")

    calls: list[str] = []

    class RecordingProvider:
        def __init__(self, *args: object, **kwargs: object) -> None:
            calls.append("constructed")

        def get_ohlcv(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("stop here")

        def get_order_book(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("stop here")

    monkeypatch.setattr("trader.cli.CcxtBinanceProvider", RecordingProvider)

    result = CliRunner().invoke(app, ["scan", "--config", config_path, "--no-ai"])

    # No credential error: --no-ai turned the stage off, so nothing needed a key. The run
    # proceeded far enough to build providers.
    assert "OPENAI_API_KEY" not in result.output
    assert calls == ["constructed"]


def test_an_unsupported_provider_stops_the_run(tmp_path) -> None:
    config_path = _write_config(tmp_path, "ai:\n  enabled: true\n  provider: gemini\n")

    result = CliRunner().invoke(app, ["scan", "--config", config_path])

    assert result.exit_code == 2
    assert "gemini" in result.output


def test_the_scan_help_documents_both_ai_switches() -> None:
    result = CliRunner().invoke(app, ["scan", "--help"])

    assert "--ai" in result.output
    assert "--no-ai" in result.output
    assert "--dry-run-ai" in result.output
