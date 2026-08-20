"""Tests for the append-only decision log.

Everything writes inside ``tmp_path`` and the clock is injected, so no test touches a real
path or a real wall clock. What matters here is that the history is *complete* — a run
that failed must leave as clear a trace as one that produced verdicts — because a gap
would bias any later measurement toward the runs that happened to work.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trader.analyst import (
    Action,
    AnalystReview,
    AnalystVerdict,
    RejectedVerdict,
)
from trader.brief import LiquidityBrief, PlanBrief, SetupBrief
from trader.decision_log import (
    SCANNER_MOVERS,
    SCANNER_SCAN,
    SCHEMA_VERSION,
    STATUS_ACCEPTED,
    STATUS_FAILED,
    STATUS_REJECTED,
    DecisionLog,
    records_for_failure,
    records_for_review,
)
from trader.direction import Direction

# A fixed instant so the stamped timestamp is assertable.
FIXED_EPOCH = 1_700_000_000.0
FIXED_ISO = "2023-11-14T22:13:20+00:00"


def _brief(
    symbol: str = "SOLUSDT",
    direction: Direction = Direction.LONG,
    total: float = 82.5,
    *,
    with_plan: bool = True,
    limited_history: bool = False,
) -> SetupBrief:
    return SetupBrief(
        symbol=symbol,
        direction=direction,
        total=total,
        categories=(),
        timeframes=(),
        plan=PlanBrief(
            entry=363.0,
            stop_loss=353.3,
            take_profit=382.4,
            risk_reward=2.0,
            invalidation=355.0,
        )
        if with_plan
        else None,
        liquidity=LiquidityBrief(
            spread_pct=0.1, bid_depth_notional=50_000.0, ask_depth_notional=40_000.0
        ),
        limited_history=limited_history,
    )


def _verdict(symbol: str = "SOLUSDT", action: Action = Action.LONG) -> AnalystVerdict:
    return AnalystVerdict(
        symbol=symbol,
        action=action,
        confidence=74.0,
        entry=100.0,
        stop_loss=94.0,
        take_profits=(112.0, 125.0),
        rationale="Trend intact with expanding volume.",
        key_risks=("BTC losing its range low",),
        invalidation="4h close below 94",
        agrees_with_engine=True,
    )


def _review(**overrides: object) -> AnalystReview:
    base: dict[str, object] = {
        "verdicts": (),
        "concentration_warning": None,
        "model": "gpt-5",
        "prompt_tokens": 2_400,
        "completion_tokens": 350,
        "estimated_cost_usd": 0.0087,
    }
    base.update(overrides)
    return AnalystReview(**base)  # type: ignore[arg-type]


def _log(tmp_path: Path, name: str = "decisions.jsonl") -> DecisionLog:
    return DecisionLog(str(tmp_path / name), clock=lambda: FIXED_EPOCH)


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_one_line_per_verdict_each_a_valid_json_object(tmp_path) -> None:
    briefs = [_brief("SOLUSDT"), _brief("ETHUSDT"), _brief("ADAUSDT")]
    accepted = [_verdict("SOLUSDT"), _verdict("ETHUSDT"), _verdict("ADAUSDT")]
    log = _log(tmp_path)

    log.append_all(records_for_review(SCANNER_SCAN, briefs, _review(), accepted, []))

    records = _lines(log.path)
    assert len(records) == 3
    assert [r["symbol"] for r in records] == ["SOLUSDT", "ETHUSDT", "ADAUSDT"]
    assert all(isinstance(r, dict) for r in records)


def test_a_record_pairs_the_engines_view_with_the_analysts(tmp_path) -> None:
    log = _log(tmp_path)

    log.append_all(
        records_for_review(SCANNER_SCAN, [_brief()], _review(), [_verdict()], [])
    )

    record = _lines(log.path)[0]
    # Provenance.
    assert record["schema_version"] == SCHEMA_VERSION
    assert record["timestamp"] == FIXED_ISO
    assert record["scanner"] == SCANNER_SCAN
    assert record["symbol"] == "SOLUSDT"
    assert record["status"] == STATUS_ACCEPTED
    # The engine's half — what the analyst is being measured against.
    assert record["engine_direction"] == "LONG"
    assert record["engine_total"] == pytest.approx(82.5)
    assert record["engine_plan"] == {
        "entry": 363.0,
        "stop_loss": 353.3,
        "take_profit": 382.4,
        "risk_reward": 2.0,
        "invalidation": 355.0,
    }
    # The analyst's half.
    assert record["action"] == "long"
    assert record["confidence"] == pytest.approx(74.0)
    assert record["entry"] == pytest.approx(100.0)
    assert record["stop_loss"] == pytest.approx(94.0)
    assert record["take_profits"] == [112.0, 125.0]
    assert record["invalidation"] == "4h close below 94"
    assert record["rationale"] == "Trend intact with expanding volume."
    assert record["key_risks"] == ["BTC losing its range low"]
    assert record["agrees_with_engine"] is True
    # What produced it and what it cost.
    assert record["model"] == "gpt-5"
    assert record["prompt_tokens"] == 2_400
    assert record["completion_tokens"] == 350
    assert record["estimated_cost_usd"] == pytest.approx(0.0087)


def test_a_rejected_verdict_is_recorded_with_its_reason(tmp_path) -> None:
    brief = _brief()
    rejected = [RejectedVerdict(_verdict(), "long stop-loss 150 is not below entry 100")]
    log = _log(tmp_path)

    log.append_all(records_for_review(SCANNER_SCAN, [brief], _review(), [], rejected))

    record = _lines(log.path)[0]
    assert record["status"] == STATUS_REJECTED
    assert record["reason"] == "long stop-loss 150 is not below entry 100"
    # The verdict itself is still recorded — what the model said is the evidence.
    assert record["action"] == "long"


def test_a_failed_review_records_one_line_per_selected_candidate(tmp_path) -> None:
    briefs = [_brief("SOLUSDT"), _brief("ETHUSDT")]
    log = _log(tmp_path)

    log.append_all(
        records_for_failure(SCANNER_SCAN, briefs, "no usable review after 3 attempts", "gpt-5")
    )

    records = _lines(log.path)
    assert len(records) == 2
    assert [r["status"] for r in records] == [STATUS_FAILED, STATUS_FAILED]
    assert all(r["reason"] == "no usable review after 3 attempts" for r in records)
    # The engine's half is still there, so the setup is not lost to the measurement.
    assert all(r["engine_total"] == pytest.approx(82.5) for r in records)
    assert all(r["action"] is None for r in records)


def test_a_candidate_the_model_skipped_is_recorded_as_failed(tmp_path) -> None:
    briefs = [_brief("SOLUSDT"), _brief("ETHUSDT")]
    log = _log(tmp_path)

    # Only one of the two candidates came back.
    log.append_all(
        records_for_review(SCANNER_SCAN, briefs, _review(), [_verdict("SOLUSDT")], [])
    )

    records = _lines(log.path)
    assert len(records) == 2
    by_symbol = {r["symbol"]: r for r in records}
    assert by_symbol["SOLUSDT"]["status"] == STATUS_ACCEPTED
    assert by_symbol["ETHUSDT"]["status"] == STATUS_FAILED
    assert "no verdict" in str(by_symbol["ETHUSDT"]["reason"])


def test_a_verdict_about_an_unsent_coin_still_leaves_a_trace(tmp_path) -> None:
    rejected = [
        RejectedVerdict(_verdict("DOGEUSDT"), "DOGEUSDT was not among the reviewed candidates")
    ]
    log = _log(tmp_path)

    log.append_all(
        records_for_review(SCANNER_SCAN, [_brief("SOLUSDT")], _review(), [], rejected)
    )

    records = _lines(log.path)
    symbols = {r["symbol"] for r in records}
    assert "DOGEUSDT" in symbols
    hallucinated = next(r for r in records if r["symbol"] == "DOGEUSDT")
    assert hallucinated["status"] == STATUS_REJECTED
    assert hallucinated["engine_direction"] == ""


def test_records_are_appended_not_overwritten(tmp_path) -> None:
    log = _log(tmp_path)

    log.append_all(
        records_for_review(SCANNER_SCAN, [_brief("SOLUSDT")], _review(), [_verdict("SOLUSDT")], [])
    )
    log.append_all(
        records_for_review(SCANNER_SCAN, [_brief("ETHUSDT")], _review(), [_verdict("ETHUSDT")], [])
    )

    records = _lines(log.path)
    assert [r["symbol"] for r in records] == ["SOLUSDT", "ETHUSDT"]


def test_a_second_process_appends_after_the_first(tmp_path) -> None:
    path = tmp_path / "decisions.jsonl"
    first = DecisionLog(str(path), clock=lambda: FIXED_EPOCH)
    second = DecisionLog(str(path), clock=lambda: FIXED_EPOCH + 3600)

    first.append_all(
        records_for_review(SCANNER_SCAN, [_brief("SOLUSDT")], _review(), [_verdict("SOLUSDT")], [])
    )
    second.append_all(
        records_for_review(SCANNER_SCAN, [_brief("ETHUSDT")], _review(), [_verdict("ETHUSDT")], [])
    )

    records = _lines(path)
    assert len(records) == 2
    # The first run's record is byte-for-byte what it was.
    assert records[0]["symbol"] == "SOLUSDT"
    assert records[0]["timestamp"] == FIXED_ISO
    assert records[1]["timestamp"] != FIXED_ISO


def test_the_parent_directory_is_created(tmp_path) -> None:
    log = DecisionLog(str(tmp_path / "nested" / "deeper" / "d.jsonl"), clock=lambda: FIXED_EPOCH)

    log.append_all(records_for_failure(SCANNER_SCAN, [_brief()], "boom"))

    assert log.path.exists()


def test_the_timestamp_comes_from_the_injected_clock(tmp_path) -> None:
    log = DecisionLog(str(tmp_path / "d.jsonl"), clock=lambda: 0.0)

    log.append_all(records_for_failure(SCANNER_SCAN, [_brief()], "boom"))

    assert _lines(log.path)[0]["timestamp"] == "1970-01-01T00:00:00+00:00"


def test_appending_nothing_writes_no_file(tmp_path) -> None:
    log = _log(tmp_path)

    log.append_all([])

    assert not log.path.exists()


def test_the_scanner_that_produced_the_candidate_is_recorded(tmp_path) -> None:
    log = _log(tmp_path)

    log.append_all(records_for_failure(SCANNER_SCAN, [_brief("SOLUSDT")], "x"))
    log.append_all(records_for_failure(SCANNER_MOVERS, [_brief("ETHUSDT")], "x"))

    records = _lines(log.path)
    assert [r["scanner"] for r in records] == [SCANNER_SCAN, SCANNER_MOVERS]


def test_the_limited_history_marker_is_carried_into_the_record(tmp_path) -> None:
    log = _log(tmp_path)

    log.append_all(records_for_failure(SCANNER_SCAN, [_brief(limited_history=True)], "x"))

    assert _lines(log.path)[0]["limited_history"] is True


def test_a_setup_with_no_engine_plan_records_a_null_plan(tmp_path) -> None:
    log = _log(tmp_path)

    log.append_all(records_for_failure(SCANNER_SCAN, [_brief(with_plan=False)], "x"))

    assert _lines(log.path)[0]["engine_plan"] is None


def test_no_credential_or_prompt_text_reaches_a_record(tmp_path) -> None:
    # A record carries the model's *answer*, never the evidence block that was sent and
    # never anything credential-shaped.
    log = _log(tmp_path)

    log.append_all(
        records_for_review(SCANNER_SCAN, [_brief()], _review(), [_verdict()], [])
    )

    raw = log.path.read_text(encoding="utf-8")
    assert "sk-" not in raw
    assert "api_key" not in raw
    assert "MARKET CONTEXT" not in raw
    assert "CANDIDATES" not in raw
    assert "ADVISORY ONLY" not in raw


def test_the_readme_documents_the_record_schema() -> None:
    # The README's fenced example is what a later measurement tool will be written
    # against, so it must not drift from what the writer actually produces.
    readme = Path("README.md").read_text(encoding="utf-8")
    marker = "<!-- decision-record-example -->"
    assert marker in readme, "README is missing the decision-record example marker"

    fenced = readme.split(marker, 1)[1].split("```json", 1)[1].split("```", 1)[0]
    documented = json.loads(fenced)

    log = DecisionLog("unused", clock=lambda: FIXED_EPOCH)
    produced = log.to_dict(
        records_for_review(SCANNER_SCAN, [_brief()], _review(), [_verdict()], [])[0]
    )

    assert set(documented) == set(produced)
