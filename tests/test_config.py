"""Unit tests for the YAML configuration layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from trader.config import (
    DEFAULT_ATR_BUFFER,
    DEFAULT_BINANCE_DELAY,
    DEFAULT_BTC_VETO,
    DEFAULT_EXCHANGE,
    DEFAULT_HTF_TIMEFRAMES,
    DEFAULT_LEAD_TIMEFRAME,
    DEFAULT_LONG_ONLY,
    DEFAULT_MAX_SPREAD,
    DEFAULT_MIN_DEPTH,
    DEFAULT_OHLCV_LOOKBACK,
    DEFAULT_PROFILE,
    DEFAULT_REFERENCE_TIMEFRAME,
    DEFAULT_REQUIRE_CONFIRMATION,
    DEFAULT_SCREENER,
    DEFAULT_TARGET_RR,
    DEFAULT_TIMEFRAMES,
    DEFAULT_TRADINGVIEW_BATCH_SIZE,
    DEFAULT_TRADINGVIEW_DELAY,
    DEFAULT_WATCHLIST,
    Config,
    ConfigError,
    ResolvedProfile,
    load_config,
    resolve_profile,
)


def _write(tmp_path: Path, text: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_none_path_returns_defaults() -> None:
    config = load_config(None)
    assert config == Config()
    assert config.watchlist == DEFAULT_WATCHLIST
    assert config.exchange == DEFAULT_EXCHANGE
    assert config.screener == DEFAULT_SCREENER
    assert config.timeframes == DEFAULT_TIMEFRAMES
    assert config.watch_interval is None
    # New multi-factor settings all fall back to their documented defaults.
    assert config.min_depth == DEFAULT_MIN_DEPTH
    assert config.max_spread == DEFAULT_MAX_SPREAD
    assert config.atr_buffer == DEFAULT_ATR_BUFFER
    assert config.target_rr == DEFAULT_TARGET_RR
    assert config.reference_timeframe == DEFAULT_REFERENCE_TIMEFRAME
    # No profile set -> futures preset preserving today's timeframes/lead/reference.
    assert config.profile == DEFAULT_PROFILE == "futures"
    assert config.timeframes == ["15m", "1h", "4h", "1d"]
    assert config.htf_timeframes == DEFAULT_HTF_TIMEFRAMES == ["4h", "1d"]
    assert config.lead_timeframe == DEFAULT_LEAD_TIMEFRAME == "1d"
    assert config.reference_timeframe == "4h"
    assert config.require_confirmation == DEFAULT_REQUIRE_CONFIRMATION is False
    assert config.btc_veto == DEFAULT_BTC_VETO is True
    assert config.binance_delay == DEFAULT_BINANCE_DELAY
    assert config.tradingview_delay == DEFAULT_TRADINGVIEW_DELAY
    assert config.ohlcv_lookback == DEFAULT_OHLCV_LOOKBACK
    assert config.long_only == DEFAULT_LONG_ONLY
@pytest.mark.parametrize(
    "key,body",
    [
        ("quality_threshold", "quality_threshold: 80"),
        ("category_weights", "category_weights:\n  trend: 25"),
        ("relative_volume_multiple", "relative_volume_multiple: 1.5"),
        ("entry", "entry:\n  quality: true"),
        ("weights", "weights:\n  4h: 1.0"),
        ("request_delay", "request_delay: 0.5"),
    ],
)
def test_a_retired_setting_is_rejected_with_an_explanation(tmp_path, key, body) -> None:
    """A removed setting must not read as a typo.

    These four were real settings that were deliberately deleted when the indicator set was
    closed to the trader's method. The generic "unknown configuration key" error would send
    a reader hunting for a spelling mistake, so each is named with what happened to it.
    """

    path = tmp_path / "config.yaml"
    path.write_text(f"watchlist: [BTCUSDT]\n{body}\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=f"'{key}' was removed"):
        load_config(str(path))




def test_valid_yaml_parses_into_expected_config(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        watchlist: [XRPUSDT, ADAUSDT]
        exchange: KRAKEN
        screener: america
        timeframes: [1h, 1d]
        watch_interval: 60
        min_depth: 100
        max_spread: 0.002
        atr_buffer: 1.0
        target_rr: 3.0
        reference_timeframe: 1d
        binance_delay: 0.5
        tradingview_delay: 3.0
        ohlcv_lookback: 500
        long_only: true
        """,
    )

    config = load_config(path)

    assert config == Config(
        watchlist=["XRPUSDT", "ADAUSDT"],
        exchange="KRAKEN",
        screener="america",
        timeframes=["1h", "1d"],
        watch_interval=60.0,
        min_depth=100.0,
        max_spread=0.002,
        atr_buffer=1.0,
        target_rr=3.0,
        reference_timeframe="1d",
        # Explicit timeframes override the futures preset; lead/htf follow the new set
        # (highest and top-two) since they are not themselves overridden here.
        htf_timeframes=["1h", "1d"],
        lead_timeframe="1d",
        binance_delay=0.5,
        tradingview_delay=3.0,
        ohlcv_lookback=500,
        long_only=True,
    )


def test_omitted_fields_receive_defaults(tmp_path: Path) -> None:
    # Only watchlist is provided; everything else must fall back to documented defaults.
    path = _write(tmp_path, "watchlist: [DOGEUSDT]\n")

    config = load_config(path)

    assert config.watchlist == ["DOGEUSDT"]
    assert config.exchange == DEFAULT_EXCHANGE
    assert config.screener == DEFAULT_SCREENER
    assert config.timeframes == DEFAULT_TIMEFRAMES
    assert config.watch_interval is None
    assert config.long_only == DEFAULT_LONG_ONLY


def test_empty_file_uses_all_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path, "")
    assert load_config(path) == Config()


def test_missing_file_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config("/nonexistent/path/to/config.yaml")


def test_malformed_yaml_raises_config_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "watchlist: [BTCUSDT\nexchange: BINANCE\n")
    with pytest.raises(ConfigError, match="could not parse YAML"):
        load_config(path)


def test_non_mapping_root_raises_config_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "- BTCUSDT\n- ETHUSDT\n")
    with pytest.raises(ConfigError, match="root must be a mapping"):
        load_config(path)


def test_wrong_type_raises_config_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "quality_threshold: not-a-number\n")
    with pytest.raises(ConfigError, match="quality_threshold"):
        load_config(path)


def test_empty_watchlist_raises_config_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "watchlist: []\n")
    with pytest.raises(ConfigError, match="non-empty list"):
        load_config(path)
def test_non_positive_watch_interval_raises_config_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "watch_interval: 0\n")
    with pytest.raises(ConfigError, match="watch_interval must be positive"):
        load_config(path)


def test_unknown_key_raises_config_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "waatchlist: [BTCUSDT]\n")
    with pytest.raises(ConfigError, match="unknown configuration key"):
        load_config(path)
def test_non_positive_target_rr_raises_config_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "target_rr: 0\n")
    with pytest.raises(ConfigError, match="target_rr must be positive"):
        load_config(path)
def test_non_positive_ohlcv_lookback_raises_config_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "ohlcv_lookback: 0\n")
    with pytest.raises(ConfigError, match="ohlcv_lookback must be positive"):
        load_config(path)


def test_non_integer_ohlcv_lookback_raises_config_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "ohlcv_lookback: 3.5\n")
    with pytest.raises(ConfigError, match="ohlcv_lookback"):
        load_config(path)


def test_non_boolean_long_only_raises_config_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "long_only: yes-please\n")
    with pytest.raises(ConfigError, match="'long_only' must be a boolean"):
        load_config(path)


@pytest.mark.parametrize("retired_key", ["buy_threshold", "sell_threshold"])
def test_retired_v1_threshold_keys_are_rejected_as_unknown(
    tmp_path: Path, retired_key: str
) -> None:
    # The v1 buy/sell thresholds are retired; a file still setting them is rejected.
    path = _write(tmp_path, f"{retired_key}: 5.0\n")
    with pytest.raises(ConfigError, match="unknown configuration key"):
        load_config(path)


# ---------------------------------------------------------------------------
# Trading profiles: resolve_profile, overrides, and validation.
# ---------------------------------------------------------------------------


def test_resolve_futures_profile_expands_to_documented_selection() -> None:
    resolved = resolve_profile("futures")
    assert resolved == ResolvedProfile(
        timeframes=["15m", "1h", "4h", "1d"],
        htf_timeframes=["4h", "1d"],
        lead_timeframe="1d",
        reference_timeframe="4h",
    )


def test_resolve_spot_profile_expands_to_documented_selection() -> None:
    resolved = resolve_profile("spot")
    assert resolved == ResolvedProfile(
        timeframes=["1d", "1w", "1M"],
        htf_timeframes=["1w", "1M"],
        lead_timeframe="1M",
        reference_timeframe="1w",
    )


def test_resolve_profile_explicit_values_override_preset() -> None:
    resolved = resolve_profile(
        "futures",
        timeframes=["1h", "4h", "1d", "1w"],
        htf_timeframes=["1d", "1w"],
        lead_timeframe="1w",
        reference_timeframe="1d",
    )
    assert resolved == ResolvedProfile(
        timeframes=["1h", "4h", "1d", "1w"],
        htf_timeframes=["1d", "1w"],
        lead_timeframe="1w",
        reference_timeframe="1d",
    )


def test_resolve_profile_rejects_unknown_profile() -> None:
    with pytest.raises(ConfigError, match="'profile' must be one of"):
        resolve_profile("swing")


def test_spot_profile_loads_from_yaml(tmp_path: Path) -> None:
    path = _write(tmp_path, "profile: spot\n")
    config = load_config(path)
    assert config.profile == "spot"
    assert config.timeframes == ["1d", "1w", "1M"]
    assert config.htf_timeframes == ["1w", "1M"]
    assert config.lead_timeframe == "1M"
    assert config.reference_timeframe == "1w"


def test_explicit_overrides_beat_the_preset_in_yaml(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        profile: spot
        timeframes: [4h, 1d, 1w]
        htf_timeframes: [1d, 1w]
        lead_timeframe: 1w
        reference_timeframe: 1d
        """,
    )
    config = load_config(path)
    assert config.profile == "spot"
    assert config.timeframes == ["4h", "1d", "1w"]
    assert config.htf_timeframes == ["1d", "1w"]
    assert config.lead_timeframe == "1w"
    assert config.reference_timeframe == "1d"


def test_invalid_profile_in_yaml_raises_config_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "profile: scalping\n")
    with pytest.raises(ConfigError, match="'profile' must be one of"):
        load_config(path)


def test_lead_timeframe_not_in_timeframes_raises_config_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "timeframes: [4h, 1d]\nlead_timeframe: 1w\n")
    with pytest.raises(ConfigError, match="lead_timeframe must be one of"):
        load_config(path)


def test_htf_timeframes_not_a_subset_raises_config_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "timeframes: [4h, 1d]\nhtf_timeframes: [1d, 1w]\n")
    with pytest.raises(ConfigError, match="htf_timeframes must be a subset"):
        load_config(path)


def test_require_confirmation_and_btc_veto_parse_with_defaults(tmp_path: Path) -> None:
    # Omitted -> documented defaults.
    assert load_config(None).require_confirmation is DEFAULT_REQUIRE_CONFIRMATION
    assert load_config(None).btc_veto is DEFAULT_BTC_VETO
    # Explicit values are parsed.
    path = _write(tmp_path, "require_confirmation: true\nbtc_veto: false\n")
    config = load_config(path)
    assert config.require_confirmation is True
    assert config.btc_veto is False


def test_non_boolean_btc_veto_raises_config_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "btc_veto: sometimes\n")
    with pytest.raises(ConfigError, match="'btc_veto' must be a boolean"):
        load_config(path)


def test_tradingview_batch_size_defaults_and_parses(tmp_path: Path) -> None:
    # Omitted -> documented default of 100.
    assert load_config(None).tradingview_batch_size == DEFAULT_TRADINGVIEW_BATCH_SIZE == 100
    # Explicit value is parsed.
    path = _write(tmp_path, "tradingview_batch_size: 50\n")
    assert load_config(path).tradingview_batch_size == 50


def test_tradingview_batch_size_below_one_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "tradingview_batch_size: 0\n")
    with pytest.raises(ConfigError, match="tradingview_batch_size must be at least 1"):
        load_config(path)


def test_tradingview_batch_size_must_be_integer(tmp_path: Path) -> None:
    path = _write(tmp_path, "tradingview_batch_size: 12.5\n")
    with pytest.raises(ConfigError, match="'tradingview_batch_size' must be an integer"):
        load_config(path)
