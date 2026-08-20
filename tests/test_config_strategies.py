"""Validation of the ``strategies:`` configuration block."""

from __future__ import annotations

import pytest

from trader.config import (
    DEFAULT_STRATEGIES_ENABLED,
    DEFAULT_STRATEGY_MAX_EXTENSION_ATR,
    ConfigError,
    StrategyConfig,
    load_config,
)


def _write(tmp_path, body: str) -> str:  # type: ignore[no-untyped-def]
    path = tmp_path / "config.yaml"
    path.write_text(f"watchlist: [BTCUSDT]\n{body}\n", encoding="utf-8")
    return str(path)


def test_the_catalogue_is_on_by_default() -> None:
    """It is the engine's only verdict now, so off by default would mean no verdict."""

    config = StrategyConfig()

    assert config.enabled is DEFAULT_STRATEGIES_ENABLED is True
    assert config.max_extension_atr == pytest.approx(DEFAULT_STRATEGY_MAX_EXTENSION_ATR)


def test_the_block_is_optional(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = load_config(_write(tmp_path, "timeframes: [4h, 1d]"))

    assert config.strategies == StrategyConfig()


def test_the_block_parses(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = load_config(
        _write(tmp_path, "strategies:\n  enabled: false\n  max_extension_atr: 3.5")
    )

    assert config.strategies.enabled is False
    assert config.strategies.max_extension_atr == pytest.approx(3.5)


@pytest.mark.parametrize("value", ["0", "0.0", "-1", "20.1"])
def test_an_out_of_range_extension_is_rejected(tmp_path, value: str) -> None:  # type: ignore[no-untyped-def]
    """Zero blocks every setup; twenty ATR can never be reached. Neither is a check."""

    with pytest.raises(ConfigError, match="strategies.max_extension_atr"):
        load_config(_write(tmp_path, f"strategies:\n  max_extension_atr: {value}"))


def test_a_misspelt_key_is_rejected_by_name(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ConfigError, match="enbaled"):
        load_config(_write(tmp_path, "strategies:\n  enbaled: true"))


def test_a_non_boolean_flag_is_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ConfigError, match="strategies.enabled"):
        load_config(_write(tmp_path, "strategies:\n  enabled: sometimes"))


def test_the_block_must_be_a_mapping(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ConfigError, match="'strategies' must be a mapping"):
        load_config(_write(tmp_path, "strategies: true"))


def test_the_shipped_configs_load_with_the_catalogue_on() -> None:
    for path in ("config.example.yaml", "mywatch.yaml"):
        assert load_config(path).strategies.enabled is True, path


# --- The Stochastic RSI turn's thresholds -----------------------------------------


def test_the_turn_thresholds_have_defaults() -> None:
    config = StrategyConfig()

    assert config.stoch_oversold == pytest.approx(0.20)
    assert config.stoch_overbought == pytest.approx(0.80)
    assert config.cross_lookback == 3


def test_the_turn_thresholds_parse(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = load_config(
        _write(
            tmp_path,
            "strategies:\n"
            "  stoch_oversold: 0.15\n"
            "  stoch_overbought: 0.85\n"
            "  cross_lookback: 5",
        )
    )

    assert config.strategies.stoch_oversold == pytest.approx(0.15)
    assert config.strategies.stoch_overbought == pytest.approx(0.85)
    assert config.strategies.cross_lookback == 5


@pytest.mark.parametrize("key", ["stoch_oversold", "stoch_overbought"])
@pytest.mark.parametrize("value", ["0", "1", "1.5", "-0.1"])
def test_a_threshold_outside_the_zero_to_one_scale_is_rejected(  # type: ignore[no-untyped-def]
    tmp_path, key: str, value: str
) -> None:
    """The Stochastic RSI is reported 0-1; a threshold outside it can never be reached."""

    with pytest.raises(ConfigError, match=f"strategies.{key}"):
        load_config(_write(tmp_path, f"strategies:\n  {key}: {value}"))


def test_an_inverted_pair_is_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ConfigError, match="must be below"):
        load_config(
            _write(tmp_path, "strategies:\n  stoch_oversold: 0.9\n  stoch_overbought: 0.1")
        )


@pytest.mark.parametrize("value", ["0", "-1", "21"])
def test_a_lookback_outside_the_detection_window_is_rejected(tmp_path, value: str) -> None:  # type: ignore[no-untyped-def]
    """A lookback wider than the detector would be silently capped, which is worse."""

    with pytest.raises(ConfigError, match="strategies.cross_lookback"):
        load_config(_write(tmp_path, f"strategies:\n  cross_lookback: {value}"))


# --- The reaction candle's thresholds ---------------------------------------------


def test_the_reaction_knobs_have_defaults() -> None:
    config = StrategyConfig()

    assert config.pattern_wick_body_ratio == pytest.approx(2.0)
    assert config.reaction_lookback == 3


def test_the_reaction_knobs_parse(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = load_config(
        _write(
            tmp_path, "strategies:\n  pattern_wick_body_ratio: 3.0\n  reaction_lookback: 5"
        )
    )

    assert config.strategies.pattern_wick_body_ratio == pytest.approx(3.0)
    assert config.strategies.reaction_lookback == 5


@pytest.mark.parametrize("value", ["1", "1.0", "0.5", "0", "21"])
def test_an_unusable_wick_ratio_is_rejected(tmp_path, value: str) -> None:  # type: ignore[no-untyped-def]
    """At or below one every wicked candle is a rejection; very large, none ever is."""

    with pytest.raises(ConfigError, match="strategies.pattern_wick_body_ratio"):
        load_config(_write(tmp_path, f"strategies:\n  pattern_wick_body_ratio: {value}"))


@pytest.mark.parametrize("value", ["0", "-1", "21"])
def test_an_out_of_range_reaction_lookback_is_rejected(tmp_path, value: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ConfigError, match="strategies.reaction_lookback"):
        load_config(_write(tmp_path, f"strategies:\n  reaction_lookback: {value}"))


# --- The trendline's thresholds ---------------------------------------------------


def test_the_trendline_knobs_have_defaults() -> None:
    config = StrategyConfig()

    assert config.trendline_min_touches == 3
    assert config.trendline_min_r2 == pytest.approx(0.7)
    assert config.trendline_tolerance_atr == pytest.approx(1.5)


def test_the_trendline_knobs_parse(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = load_config(
        _write(
            tmp_path,
            "strategies:\n"
            "  trendline_min_touches: 4\n"
            "  trendline_min_r2: 0.85\n"
            "  trendline_tolerance_atr: 2.5",
        )
    )

    assert config.strategies.trendline_min_touches == 4
    assert config.strategies.trendline_min_r2 == pytest.approx(0.85)
    assert config.strategies.trendline_tolerance_atr == pytest.approx(2.5)


@pytest.mark.parametrize("value", ["2", "0", "21"])
def test_too_few_touches_to_make_a_line_is_rejected(tmp_path, value: str) -> None:  # type: ignore[no-untyped-def]
    """Two points fit perfectly by definition, so they cannot evidence a trendline."""

    with pytest.raises(ConfigError, match="strategies.trendline_min_touches"):
        load_config(_write(tmp_path, f"strategies:\n  trendline_min_touches: {value}"))


@pytest.mark.parametrize("value", ["-0.1", "1.5"])
def test_a_fit_quality_outside_zero_to_one_is_rejected(tmp_path, value: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ConfigError, match="strategies.trendline_min_r2"):
        load_config(_write(tmp_path, f"strategies:\n  trendline_min_r2: {value}"))


@pytest.mark.parametrize("value", ["0", "-1", "21"])
def test_an_out_of_range_trendline_tolerance_is_rejected(tmp_path, value: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ConfigError, match="strategies.trendline_tolerance_atr"):
        load_config(_write(tmp_path, f"strategies:\n  trendline_tolerance_atr: {value}"))


# --- The review floor and the evidence candles ------------------------------------


def test_the_review_knobs_have_defaults() -> None:
    config = StrategyConfig()

    assert config.review_within == 2
    assert config.evidence_candles == 30


def test_the_review_knobs_parse(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = load_config(
        _write(tmp_path, "strategies:\n  review_within: 0\n  evidence_candles: 60")
    )

    assert config.strategies.review_within == 0
    assert config.strategies.evidence_candles == 60


@pytest.mark.parametrize("value", ["-1", "8"])
def test_an_out_of_range_review_floor_is_rejected(tmp_path, value: str) -> None:  # type: ignore[no-untyped-def]
    """Negative admits nothing; more than the checklist length admits everything."""

    with pytest.raises(ConfigError, match="strategies.review_within"):
        load_config(_write(tmp_path, f"strategies:\n  review_within: {value}"))


@pytest.mark.parametrize("value", ["-1", "201"])
def test_an_out_of_range_candle_count_is_rejected(tmp_path, value: str) -> None:  # type: ignore[no-untyped-def]
    """The prompt is paid for by the token, so the count is bounded."""

    with pytest.raises(ConfigError, match="strategies.evidence_candles"):
        load_config(_write(tmp_path, f"strategies:\n  evidence_candles: {value}"))
