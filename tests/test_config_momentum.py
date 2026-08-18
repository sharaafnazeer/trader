"""Tests for the momentum ("movers") configuration keys: defaults, parsing, validation.

Covers the four momentum settings — factor weights, threshold, and the two lookback
windows — proving each parses with its documented default, a supplied value is honored,
invalid values raise a clear :class:`ConfigError`, and an unknown key is rejected.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trader.config import (
    DEFAULT_BREAKOUT_LOOKBACK_DAYS,
    DEFAULT_MOMENTUM_THRESHOLD,
    DEFAULT_MOMENTUM_WEIGHTS,
    DEFAULT_RS_LOOKBACK_DAYS,
    MOMENTUM_FACTOR_BREAKOUT,
    MOMENTUM_FACTOR_RELATIVE_STRENGTH,
    ConfigError,
    load_config,
)


def _write(tmp_path: Path, text: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_momentum_defaults_when_absent() -> None:
    config = load_config(None)
    assert config.momentum_weights == DEFAULT_MOMENTUM_WEIGHTS
    assert config.momentum_threshold == DEFAULT_MOMENTUM_THRESHOLD == 60.0
    assert config.rs_lookback_days == DEFAULT_RS_LOOKBACK_DAYS == 30
    assert config.breakout_lookback_days == DEFAULT_BREAKOUT_LOOKBACK_DAYS == 20


def test_momentum_values_parse(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        momentum_weights:
          relative_strength: 50
          breakout: 25
          volume: 15
          acceleration: 10
        momentum_threshold: 70
        rs_lookback_days: 45
        breakout_lookback_days: 14
        """,
    )
    config = load_config(path)
    assert config.momentum_weights[MOMENTUM_FACTOR_RELATIVE_STRENGTH] == 50.0
    assert config.momentum_weights[MOMENTUM_FACTOR_BREAKOUT] == 25.0
    assert config.momentum_threshold == 70.0
    assert config.rs_lookback_days == 45
    assert config.breakout_lookback_days == 14


def test_negative_momentum_weight_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "momentum_weights:\n  relative_strength: -1\n")
    with pytest.raises(ConfigError, match="momentum weight"):
        load_config(path)


def test_empty_momentum_weights_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "momentum_weights: {}\n")
    with pytest.raises(ConfigError, match="momentum_weights"):
        load_config(path)


def test_threshold_out_of_range_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "momentum_threshold: 150\n")
    with pytest.raises(ConfigError, match="momentum_threshold"):
        load_config(path)


def test_non_positive_lookback_rejected(tmp_path: Path) -> None:
    rs_path = _write(tmp_path, "rs_lookback_days: 0\n")
    with pytest.raises(ConfigError, match="rs_lookback_days"):
        load_config(rs_path)
    brk_path = _write(tmp_path, "breakout_lookback_days: 0\n")
    with pytest.raises(ConfigError, match="breakout_lookback_days"):
        load_config(brk_path)


def test_unknown_momentum_key_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "momentum_lookback_days: 30\n")
    with pytest.raises(ConfigError, match="unknown configuration key"):
        load_config(path)
