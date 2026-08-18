"""Light CLI test for ``refresh-cache`` (no network; monkeypatched fake provider).

Proves the command wiring end-to-end: it builds the historical provider with the
configured cache dir, runs the refresher, prints the summary and exits 0; and that its
``--help`` documents scheduling the command externally via cron/launchd (no in-app
scheduler).
"""

from __future__ import annotations

import re

import pandas as pd
from typer.testing import CliRunner

from trader.cli import app
from trader.market_data import OHLCV_COLUMNS, Candles


def test_refresh_cache_runs_over_fake_provider_and_exits_zero(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import trader.cli as cli_module

    constructed: dict[str, object] = {}

    class _FakeHistorical:
        def __init__(self, *, cache_dir: object, **_: object) -> None:
            constructed["cache_dir"] = cache_dir

        def get_history(self, symbol: str, timeframe: str, start: int, end: int) -> Candles:
            frame = pd.DataFrame(
                [[float(start), 100.0, 101.0, 99.0, 100.5, 1_000.0]], columns=OHLCV_COLUMNS
            )
            return Candles(symbol=symbol, timeframe=timeframe, frame=frame)

    monkeypatch.setattr(cli_module, "CcxtHistoricalDataProvider", _FakeHistorical)

    cache = tmp_path / "cache"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "\n".join(
            [
                "watchlist: [AAA]",
                "timeframes: [1h]",
                "reference_timeframe: 1h",
                "lead_timeframe: 1h",
                "htf_timeframes: [1h]",
                "backtest:",
                f"  cache_dir: {cache}",
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app, ["refresh-cache", "--config", str(config_file)], env={"COLUMNS": "200"}
    )
    assert result.exit_code == 0, result.output
    assert constructed["cache_dir"] == str(cache)  # wired with the configured cache dir
    assert "Cache refresh" in result.output
    assert "Coins refreshed" in result.output


def test_refresh_cache_help_documents_cron_and_launchd_scheduling() -> None:
    result = CliRunner().invoke(app, ["refresh-cache", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output).lower()
    assert "cron" in plain
    assert "launchd" in plain
