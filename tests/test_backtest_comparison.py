"""Tests for `backtest --baseline`: the harness every later measurement depends on.

The comparison function is pure over two summary mappings, so almost everything here runs
against hand-written dicts with no backtest at all. That matters: a measurement tool whose
own tests take an hour would not get run.
"""

from __future__ import annotations

import json

import pytest
from rich.console import Console
from typer.testing import CliRunner

from trader.cli import (
    BaselineError,
    _render_comparison,
    app,
    load_baseline_summary,
    run_metadata,
)
from trader.config import Config
from trader.metrics import compare_summaries


def _summary(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "resolved_trades": 100,
        "win_rate": 0.40,
        "expectancy": 0.10,
        "profit_factor": 1.20,
        "max_drawdown": 0.50,
    }
    base.update(overrides)
    return base


def _by_name(deltas) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {d.name: d for d in deltas}


# --- The pure comparison --------------------------------------------------------


def test_every_compared_metric_is_reported() -> None:
    deltas = compare_summaries(_summary(), _summary())

    assert [d.name for d in deltas] == [
        "resolved_trades",
        "win_rate",
        "expectancy",
        "profit_factor",
        "max_drawdown",
    ]


def test_signed_differences_are_computed() -> None:
    deltas = _by_name(
        compare_summaries(
            _summary(),
            _summary(resolved_trades=120, win_rate=0.45, expectancy=0.15,
                     profit_factor=1.35, max_drawdown=0.40),
        )
    )

    assert deltas["resolved_trades"].delta == pytest.approx(20.0)
    assert deltas["win_rate"].delta == pytest.approx(0.05)
    assert deltas["expectancy"].delta == pytest.approx(0.05)
    assert deltas["profit_factor"].delta == pytest.approx(0.15)
    assert deltas["max_drawdown"].delta == pytest.approx(-0.10)


@pytest.mark.parametrize("metric", ["win_rate", "expectancy", "profit_factor"])
def test_a_rise_in_a_higher_is_better_metric_is_an_improvement(metric: str) -> None:
    up = _by_name(compare_summaries(_summary(), _summary(**{metric: 99.0})))
    down = _by_name(compare_summaries(_summary(), _summary(**{metric: -99.0})))

    assert up[metric].improved is True
    assert down[metric].improved is False


def test_drawdown_is_better_when_it_falls() -> None:
    # The one metric where down is good; scoring it like the others would invert the table.
    smaller = _by_name(compare_summaries(_summary(), _summary(max_drawdown=0.30)))
    bigger = _by_name(compare_summaries(_summary(), _summary(max_drawdown=0.70)))

    assert smaller["max_drawdown"].improved is True
    assert bigger["max_drawdown"].improved is False


def test_trade_count_has_no_verdict() -> None:
    # More trades is neither good nor bad on its own; claiming otherwise would mislead.
    deltas = _by_name(compare_summaries(_summary(), _summary(resolved_trades=500)))

    assert deltas["resolved_trades"].delta == pytest.approx(400.0)
    assert deltas["resolved_trades"].improved is None


def test_an_unchanged_metric_has_no_verdict() -> None:
    deltas = _by_name(compare_summaries(_summary(), _summary()))

    assert all(d.improved is None for d in deltas.values())
    assert deltas["expectancy"].delta == pytest.approx(0.0)


def test_a_missing_or_non_numeric_metric_is_reported_as_incomparable() -> None:
    # profit_factor is serialized as null when it is infinite, which must not crash the rest.
    deltas = _by_name(compare_summaries(_summary(), _summary(profit_factor=None)))

    assert deltas["profit_factor"].delta is None
    assert deltas["profit_factor"].improved is None
    assert deltas["expectancy"].delta == pytest.approx(0.0)

    missing = _by_name(compare_summaries({}, _summary()))
    assert missing["win_rate"].delta is None


def test_the_comparison_does_not_mutate_its_inputs() -> None:
    baseline, current = _summary(), _summary(win_rate=0.5)
    before = (dict(baseline), dict(current))

    compare_summaries(baseline, current)

    assert (baseline, current) == before


# --- Rendering ------------------------------------------------------------------


def _rendered(baseline: dict[str, object], current: dict[str, object]) -> str:
    console = Console(width=200, record=True)
    _render_comparison(compare_summaries(baseline, current), "base.json", console)
    return console.export_text()


def test_improvement_and_regression_render_with_distinct_markers() -> None:
    better = _rendered(_summary(), _summary(expectancy=0.20))
    worse = _rendered(_summary(), _summary(expectancy=0.02))

    assert "BETTER" in better and "WORSE" not in better
    assert "WORSE" in worse and "BETTER" not in worse


def test_the_change_column_is_signed() -> None:
    assert "+0.100" in _rendered(_summary(), _summary(expectancy=0.20))
    assert "-0.080" in _rendered(_summary(), _summary(expectancy=0.02))


def test_percentage_metrics_render_as_percentages() -> None:
    text = _rendered(_summary(), _summary(win_rate=0.45))

    assert "40.0" in text and "45.0" in text and "+5.0" in text


def test_the_baseline_path_is_named_in_the_table() -> None:
    assert "base.json" in _rendered(_summary(), _summary())


# --- Loading and comparability --------------------------------------------------


def _config(watchlist: list[str] | None = None) -> Config:
    return Config(watchlist=watchlist or ["AAA", "BBB"])


def _write_report(tmp_path, run: dict[str, object] | None, name: str = "base.json") -> str:
    payload: dict[str, object] = {"summary": _summary(), "trades": []}
    if run is not None:
        payload["run"] = run
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_a_comparable_baseline_loads(tmp_path) -> None:
    run = run_metadata(_config(), 1_000, 2_000)
    path = _write_report(tmp_path, run)

    assert load_baseline_summary(path, run)["expectancy"] == pytest.approx(0.10)


def test_a_missing_file_is_reported_with_its_path(tmp_path) -> None:
    missing = str(tmp_path / "nope.json")

    with pytest.raises(BaselineError) as excinfo:
        load_baseline_summary(missing, run_metadata(_config(), 1_000, 2_000))

    assert missing in str(excinfo.value)


def test_a_malformed_file_is_reported_not_raised_raw(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json at all", encoding="utf-8")

    with pytest.raises(BaselineError, match="not valid JSON"):
        load_baseline_summary(str(path), run_metadata(_config(), 1_000, 2_000))


def test_a_report_without_a_summary_is_rejected(tmp_path) -> None:
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"trades": []}), encoding="utf-8")

    with pytest.raises(BaselineError, match="no 'summary'"):
        load_baseline_summary(str(path), run_metadata(_config(), 1_000, 2_000))


def test_a_report_predating_run_metadata_is_rejected(tmp_path) -> None:
    # Comparing against a report whose provenance is unknown would manufacture findings.
    path = _write_report(tmp_path, None)

    with pytest.raises(BaselineError, match="predates run metadata"):
        load_baseline_summary(path, run_metadata(_config(), 1_000, 2_000))


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("window_start_ms", {"window_start_ms": 999}),
        ("window_end_ms", {"window_end_ms": 999}),
        ("watchlist_size", {"watchlist_size": 87}),
        ("fee_rate", {"fee_rate": 0.001}),
        ("slippage", {"slippage": 0.002}),
    ],
)
def test_an_incomparable_baseline_is_rejected_naming_the_mismatch(
    tmp_path, field: str, changed: dict[str, object]
) -> None:
    current = run_metadata(_config(), 1_000, 2_000)
    stale = {**current, **changed}
    path = _write_report(tmp_path, stale)

    with pytest.raises(BaselineError) as excinfo:
        load_baseline_summary(path, current)

    message = str(excinfo.value)
    assert "not comparable" in message
    assert field in message


def test_run_metadata_records_what_produced_the_numbers() -> None:
    meta = run_metadata(_config(["AAA", "BBB", "CCC"]), 111, 222)

    assert meta["window_start_ms"] == 111
    assert meta["window_end_ms"] == 222
    assert meta["watchlist_size"] == 3
    for key in ("fee_rate", "slippage", "risk_per_trade"):
        assert key in meta


# --- Command surface ------------------------------------------------------------


def test_the_backtest_help_documents_the_baseline_option() -> None:
    result = CliRunner().invoke(app, ["backtest", "--help"])

    assert "--baseline" in result.output


def test_a_missing_baseline_exits_non_zero_from_the_command(tmp_path) -> None:
    config_file = tmp_path / "c.yaml"
    config_file.write_text(
        "\n".join(
            [
                "watchlist: [AAA]",
                "timeframes: [1h, 4h]",
                "reference_timeframe: 4h",
                "lead_timeframe: 4h",
                "htf_timeframes: [4h]",
            ]
        ),
        encoding="utf-8",
    )
    absent = str(tmp_path / "absent.json")

    result = CliRunner().invoke(
        app, ["backtest", "--config", str(config_file), "--demo", "--baseline", absent]
    )

    assert result.exit_code == 2
    assert absent in result.output
