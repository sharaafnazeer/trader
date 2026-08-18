"""Tests for the CLI `backtest` path over an injected, network-free history.

Exercises the CLI wiring with a hand-built history (no network) and captured console
output: the resolved trade count and win rate are printed, the run exits 0, and the
past-performance disclaimer is present. The command's registration is verified via
`backtest --help`, and a real invocation over the built-in synthetic history exits 0.
"""

from __future__ import annotations

import csv
import json
import re

import pandas as pd
from rich.console import Console
from typer.testing import CliRunner

from trader.cli import (
    app,
    backtest_report_to_dict,
    demo_history,
    run_backtest,
    write_trade_log_csv,
)
from trader.config import Config
from trader.direction import Direction
from trader.market_data import Candles
from trader.metrics import summarize
from trader.trade_simulator import TradeOutcome, TradeResult

_HOUR = 3_600_000


def _bullish_frame(symbol: str, timeframe: str, step_ms: int, rows: int) -> Candles:
    data = []
    base = 100.0
    for i in range(rows):
        base += 1.0
        offset = {0: 0.0, 1: 3.0, 2: 5.0, 3: 3.0, 4: 0.0, 5: -2.0}[i % 6]
        close = base + offset
        data.append([i * step_ms, close - 0.5, close + 1.0, close - 1.0, close, 1_000.0 + i])
    frame = pd.DataFrame(
        data, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    return Candles(symbol=symbol, timeframe=timeframe, frame=frame)


def _aligned(symbol: str, reference_bars: int = 80) -> dict[str, Candles]:
    span = 4 * _HOUR * reference_bars
    return {
        "1h": _bullish_frame(symbol, "1h", _HOUR, span // _HOUR),
        "4h": _bullish_frame(symbol, "4h", 4 * _HOUR, reference_bars),
    }


def _config() -> Config:
    return Config(
        watchlist=["AAA"],
        timeframes=["1h", "4h"],
        reference_timeframe="4h",
        lead_timeframe="4h",
        htf_timeframes=["4h"],
        quality_threshold=0.0,
    )


def test_run_backtest_prints_count_and_win_rate() -> None:
    config = _config()
    histories = {"AAA": _aligned("AAA")}
    btc_frames = _aligned("BTC/USDT")

    console = Console(width=200, force_terminal=False)
    with console.capture() as capture:
        report = run_backtest(histories, btc_frames, config, console)
    output = capture.get()

    assert "Backtest results" in output
    assert "Resolved trades" in output
    assert "Win rate" in output
    assert "past performance" in output.lower()
    # The bullish path resolves exactly one winning trade.
    assert report.resolved_trades == 1
    assert report.wins == 1


def test_demo_history_covers_the_watchlist_and_btc() -> None:
    config = _config()
    histories, btc_frames = demo_history(config, reference_bars=40)
    assert set(histories) == {"AAA"}
    assert set(histories["AAA"]) == {"1h", "4h"}
    assert set(btc_frames) == {"1h", "4h"}
    # Timeframes are aligned to a common span: the 1h frame has ~4x the 4h bars.
    assert len(histories["AAA"]["4h"].frame) == 40
    assert len(histories["AAA"]["1h"].frame) == 160


def test_backtest_command_help_exits_zero() -> None:
    result = CliRunner().invoke(app, ["backtest", "--help"])
    assert result.exit_code == 0
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "--config" in plain


def test_backtest_command_runs_over_injected_history_exit_zero(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "\n".join(
            [
                "watchlist: [AAA]",
                "timeframes: [1h, 4h]",
                "reference_timeframe: 4h",
                "lead_timeframe: 4h",
                "htf_timeframes: [4h]",
                "quality_threshold: 0.0",
            ]
        ),
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["backtest", "--config", str(config_file), "--demo"])
    assert result.exit_code == 0, result.output
    assert "past performance" in result.output.lower()


def test_report_renders_metric_labels_and_disclaimer() -> None:
    config = _config()
    histories = {"AAA": _aligned("AAA")}
    btc_frames = _aligned("BTC/USDT")

    console = Console(width=250, force_terminal=False)
    with console.capture() as capture:
        run_backtest(histories, btc_frames, config, console)
    output = capture.get().lower()

    for label in ("win rate", "expectancy", "profit factor", "max drawdown"):
        assert label in output
    assert "past performance" in output


def _pinned_outcomes() -> tuple[TradeOutcome, ...]:
    def out(symbol: str, direction: Direction, result: TradeResult, r: float, t: int):  # type: ignore[no-untyped-def]
        resolved = result is not TradeResult.UNRESOLVED
        return TradeOutcome(
            symbol=symbol,
            direction=direction,
            entry_time=t,
            entry_price=100.0,
            exit_time=t + 1 if resolved else None,
            exit_price=110.0 if resolved else None,
            result=result,
            r_multiple=r if resolved else 0.0,
            costs=0.1 if resolved else 0.0,
        )

    return (
        out("AAA", Direction.LONG, TradeResult.WIN, 2.0, 1),
        out("BBB", Direction.SHORT, TradeResult.LOSS, -1.0, 2),
        out("AAA", Direction.LONG, TradeResult.UNRESOLVED, 0.0, 3),
    )


def test_json_export_summary_and_trade_log_reconcile() -> None:
    outcomes = _pinned_outcomes()
    report = summarize(outcomes, risk_per_trade=0.1)

    doc = backtest_report_to_dict(report, outcomes)

    # One trade row per simulated trade with the required fields.
    trades = doc["trades"]
    assert len(trades) == len(outcomes)
    required = {
        "symbol",
        "direction",
        "entry_time",
        "entry_price",
        "exit_time",
        "exit_price",
        "result",
        "r_multiple",
        "costs",
    }
    assert all(required <= set(row) for row in trades)

    # Wins/losses in the log reconcile with the summary totals.
    wins = sum(1 for r in trades if r["result"] == "WIN")
    losses = sum(1 for r in trades if r["result"] == "LOSS")
    summary = doc["summary"]
    assert wins == summary["wins"] == report.wins
    assert losses == summary["losses"] == report.losses
    # The dict must be valid JSON (no inf); profit_factor here is finite.
    round_tripped = json.loads(json.dumps(doc))
    assert round_tripped["summary"]["resolved_trades"] == report.resolved_trades


def test_csv_export_one_row_per_trade_reconciles(tmp_path) -> None:  # type: ignore[no-untyped-def]
    outcomes = _pinned_outcomes()
    report = summarize(outcomes, risk_per_trade=0.1)
    path = tmp_path / "trades.csv"

    write_trade_log_csv(str(path), outcomes)

    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == len(outcomes)
    wins = sum(1 for r in rows if r["result"] == "WIN")
    losses = sum(1 for r in rows if r["result"] == "LOSS")
    assert wins == report.wins
    assert losses == report.losses
    assert set(rows[0]) == {
        "symbol",
        "direction",
        "entry_time",
        "entry_price",
        "exit_time",
        "exit_price",
        "result",
        "r_multiple",
        "costs",
    }


def _range_config_file(tmp_path):  # type: ignore[no-untyped-def]
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "\n".join(
            [
                "watchlist: [AAA]",
                "timeframes: [1h, 4h]",
                "reference_timeframe: 4h",
                "lead_timeframe: 4h",
                "htf_timeframes: [4h]",
                "quality_threshold: 0.0",
            ]
        ),
        encoding="utf-8",
    )
    return config_file


def test_backtest_command_with_from_to_exits_zero_with_labels(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config_file = _range_config_file(tmp_path)
    result = CliRunner().invoke(
        app,
        ["backtest", "--config", str(config_file), "--from", "2024-01-01", "--to", "2024-06-30",
         "--demo"],
        env={"COLUMNS": "250"},
    )
    assert result.exit_code == 0, result.output
    lower = result.output.lower()
    assert "past performance" in lower
    for label in ("win rate", "expectancy", "profit factor", "max drawdown"):
        assert label in lower


def test_backtest_command_from_after_to_errors(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config_file = _range_config_file(tmp_path)
    result = CliRunner().invoke(
        app,
        ["backtest", "--config", str(config_file), "--from", "2024-07-01", "--to", "2024-01-01"],
    )
    assert result.exit_code == 2


def test_backtest_command_writes_json_and_csv(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "\n".join(
            [
                "watchlist: [AAA]",
                "timeframes: [1h, 4h]",
                "reference_timeframe: 4h",
                "lead_timeframe: 4h",
                "htf_timeframes: [4h]",
                "quality_threshold: 0.0",
            ]
        ),
        encoding="utf-8",
    )
    json_out = tmp_path / "out.json"
    csv_out = tmp_path / "out.csv"
    result = CliRunner().invoke(
        app,
        [
            "backtest",
            "--config",
            str(config_file),
            "--json",
            str(json_out),
            "--csv",
            str(csv_out),
            "--demo",
        ],
    )
    assert result.exit_code == 0, result.output
    doc = json.loads(json_out.read_text(encoding="utf-8"))
    assert "summary" in doc and "trades" in doc
    with open(csv_out, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    # The CSV trade log reconciles with the JSON summary totals.
    resolved = sum(1 for r in rows if r["result"] in {"WIN", "LOSS"})
    assert resolved == doc["summary"]["resolved_trades"]


def test_backtest_command_default_uses_live_ccxt_provider(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Without --demo, the command builds the live CcxtHistoricalDataProvider with the
    configured cache dir. Patched to a network-free fake returning empty history, so every
    coin is gracefully skipped and the run still exits 0 — proving the wiring, not a fetch.
    """
    import trader.cli as cli_module
    from trader.market_data import OHLCV_COLUMNS

    constructed: dict[str, object] = {}

    class _FakeHistorical:
        def __init__(self, *, cache_dir: object, **_: object) -> None:
            constructed["cache_dir"] = cache_dir

        def get_history(self, symbol: str, timeframe: str, start: int, end: int) -> Candles:
            return Candles(
                symbol=symbol,
                timeframe=timeframe,
                frame=pd.DataFrame(columns=OHLCV_COLUMNS),
            )

    monkeypatch.setattr(cli_module, "CcxtHistoricalDataProvider", _FakeHistorical)

    cache = tmp_path / "cache"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "\n".join(
            [
                "watchlist: [AAA]",
                "timeframes: [1h, 4h]",
                "reference_timeframe: 4h",
                "lead_timeframe: 4h",
                "htf_timeframes: [4h]",
                "backtest:",
                f"  cache_dir: {cache}",
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["backtest", "--config", str(config_file), "--from", "2024-01-01", "--to", "2024-02-01"],
    )
    assert result.exit_code == 0, result.output
    # The default (non-demo) path wired the live provider with the configured cache dir.
    assert constructed["cache_dir"] == str(cache)
