"""Tests for the ``backtest`` configuration section: defaults, parsing, validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from trader.config import (
    DEFAULT_BACKTEST_CACHE_DIR,
    DEFAULT_BACKTEST_FEE_RATE,
    DEFAULT_BACKTEST_MAX_WORKERS,
    DEFAULT_BACKTEST_RISK_PER_TRADE,
    DEFAULT_BACKTEST_SLIPPAGE,
    DEFAULT_HISTORY_HORIZON_DAYS,
    BacktestConfig,
    ConfigError,
    load_config,
    parse_iso_date,
)


def _write(tmp_path: Path, text: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_backtest_defaults_when_absent() -> None:
    config = load_config(None)
    assert config.backtest == BacktestConfig()
    assert config.backtest.fee_rate == DEFAULT_BACKTEST_FEE_RATE
    assert config.backtest.slippage == DEFAULT_BACKTEST_SLIPPAGE
    assert config.backtest.risk_per_trade == DEFAULT_BACKTEST_RISK_PER_TRADE
    assert config.backtest.max_holding_bars is None
    assert config.backtest.start is None and config.backtest.end is None
    assert config.backtest.cache_dir == DEFAULT_BACKTEST_CACHE_DIR
    assert config.backtest.max_workers == DEFAULT_BACKTEST_MAX_WORKERS == 5
    assert config.backtest.history_horizon_days == DEFAULT_HISTORY_HORIZON_DAYS == 730


def test_backtest_section_parses(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "\n".join(
            [
                "backtest:",
                "  fee_rate: 0.001",
                "  slippage: 0.002",
                "  risk_per_trade: 0.02",
                "  max_holding_bars: 48",
                "  start: 2024-01-01",
                "  end: 2024-06-30",
                "  cache_dir: /tmp/bt-cache",
            ]
        ),
    )
    bt = load_config(path).backtest
    assert bt.fee_rate == 0.001
    assert bt.slippage == 0.002
    assert bt.risk_per_trade == 0.02
    assert bt.max_holding_bars == 48
    assert bt.start == "2024-01-01"
    assert bt.end == "2024-06-30"
    assert bt.cache_dir == "/tmp/bt-cache"


def test_negative_fee_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "backtest:\n  fee_rate: -0.01\n")
    with pytest.raises(ConfigError, match="fee_rate"):
        load_config(path)


def test_risk_per_trade_out_of_range_rejected(tmp_path: Path) -> None:
    for value in ("0.0", "1.5", "-0.1"):
        path = _write(tmp_path, f"backtest:\n  risk_per_trade: {value}\n")
        with pytest.raises(ConfigError, match="risk_per_trade"):
            load_config(path)


def test_start_after_end_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "backtest:\n  start: 2024-07-01\n  end: 2024-01-01\n")
    with pytest.raises(ConfigError, match="start must not be after end"):
        load_config(path)


def test_unparseable_date_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "backtest:\n  start: not-a-date\n")
    with pytest.raises(ConfigError, match="ISO date"):
        load_config(path)


def test_max_workers_parses(tmp_path: Path) -> None:
    path = _write(tmp_path, "backtest:\n  max_workers: 8\n")
    assert load_config(path).backtest.max_workers == 8


def test_max_workers_below_one_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "backtest:\n  max_workers: 0\n")
    with pytest.raises(ConfigError, match="max_workers must be at least 1"):
        load_config(path)


def test_max_workers_must_be_integer(tmp_path: Path) -> None:
    path = _write(tmp_path, "backtest:\n  max_workers: 2.5\n")
    with pytest.raises(ConfigError, match="'max_workers' must be an integer"):
        load_config(path)


def test_history_horizon_days_parses(tmp_path: Path) -> None:
    path = _write(tmp_path, "backtest:\n  history_horizon_days: 365\n")
    assert load_config(path).backtest.history_horizon_days == 365


def test_history_horizon_days_non_positive_rejected(tmp_path: Path) -> None:
    for value in ("0", "-30"):
        path = _write(tmp_path, f"backtest:\n  history_horizon_days: {value}\n")
        with pytest.raises(ConfigError, match="history_horizon_days must be positive"):
            load_config(path)


def test_history_horizon_days_must_be_integer(tmp_path: Path) -> None:
    path = _write(tmp_path, "backtest:\n  history_horizon_days: 1.5\n")
    with pytest.raises(ConfigError, match="'history_horizon_days' must be an integer"):
        load_config(path)


def test_unknown_backtest_key_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "backtest:\n  leverage: 5\n")
    with pytest.raises(ConfigError, match="unknown backtest configuration key"):
        load_config(path)


def test_parse_iso_date_utc_midnight_ms() -> None:
    # 2024-01-01T00:00:00Z is 1_704_067_200_000 ms since the epoch.
    assert parse_iso_date("2024-01-01", "start") == 1_704_067_200_000
