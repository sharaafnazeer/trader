"""Typed YAML configuration for the analysis core.

:func:`load_config` reads a YAML file into a validated, typed :class:`Config`.
Every field has a documented default, so an omitted field (or no file at all)
still yields a usable configuration. Malformed YAML or an out-of-range/invalid
value raises :class:`ConfigError` rather than producing a misleading result.

This module belongs to the analysis core; it does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import yaml

# Documented built-in defaults. Kept in sync with the pre-configuration Runner
# defaults so "no config file" behaves identically to an empty config file.
DEFAULT_EXCHANGE = "BINANCE"
DEFAULT_SCREENER = "crypto"
DEFAULT_WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
DEFAULT_TIMEFRAMES = ["15m", "1h", "4h", "1d"]
DEFAULT_WEIGHTS: dict[str, float] = {"15m": 1.0, "1h": 2.0, "4h": 3.0, "1d": 4.0}
DEFAULT_REQUEST_DELAY = 1.0
DEFAULT_WATCH_INTERVAL: float | None = None

# Per-source request delays (seconds). TradingView rate-limits aggressively so it is
# spaced out far more than Binance's public market-data endpoints. Kept in sync with
# the Runner's per-source throttle defaults.
DEFAULT_BINANCE_DELAY = 0.25
DEFAULT_TRADINGVIEW_DELAY = 2.0

# Number of OHLCV candles fetched per (symbol, timeframe); enough history for EMA200.
DEFAULT_OHLCV_LOOKBACK = 300

# Max symbols per batched TradingView request; a larger watchlist fans out into several
# merged calls. TradingView accepts many symbols per request, so 100 is a safe default.
DEFAULT_TRADINGVIEW_BATCH_SIZE = 100

# Restrict surfaced setups to longs only when enabled; both directions by default.
DEFAULT_LONG_ONLY = False

# Scoring-model category keys. Defined here (the configuration owner) so the pure
# ``scoring_model`` can reference the same names without config importing the heavy
# indicator stack. The eight categories sum to 100 once all are implemented; this
# phase's ``ScoringModel`` scores only the feature/structure/BTC-driven subset.
CATEGORY_TREND = "trend"
CATEGORY_STRUCTURE = "structure"
CATEGORY_MOMENTUM = "momentum"
CATEGORY_VOLUME = "volume"
CATEGORY_BREAKOUT = "breakout"
CATEGORY_BTC = "btc_alignment"
CATEGORY_LIQUIDITY = "liquidity"
CATEGORY_RISK_REWARD = "risk_reward"

# Default per-category weights (points out of 100). Kept as in-code defaults for this
# phase; task 07 lifts the mapping onto the validated YAML config surface.
DEFAULT_CATEGORY_WEIGHTS: dict[str, float] = {
    CATEGORY_TREND: 25.0,
    CATEGORY_STRUCTURE: 20.0,
    CATEGORY_MOMENTUM: 15.0,
    CATEGORY_VOLUME: 15.0,
    CATEGORY_BREAKOUT: 10.0,
    CATEGORY_BTC: 5.0,
    CATEGORY_LIQUIDITY: 5.0,
    CATEGORY_RISK_REWARD: 5.0,
}

# The single quality gate that replaces v1's buy/sell thresholds: a coin surfaces only
# when its total meets or exceeds this. In-code default for now (task 07 adds YAML).
DEFAULT_QUALITY_THRESHOLD = 75.0

# Momentum / breakout scanner ("movers") settings. This is a second, independent
# scanner alongside the trend ``scan``; these keys live only on the momentum path and
# do not touch the trend scoring model. The four momentum factor keys mirror the
# fractional-credit × weight pattern of ``category_weights`` above.
MOMENTUM_FACTOR_RELATIVE_STRENGTH = "relative_strength"
MOMENTUM_FACTOR_BREAKOUT = "breakout"
MOMENTUM_FACTOR_VOLUME = "volume"
MOMENTUM_FACTOR_ACCELERATION = "acceleration"

# Default momentum factor weights (points out of 100): relative strength vs BTC leads,
# then breakout, then volume expansion, then acceleration.
DEFAULT_MOMENTUM_WEIGHTS: dict[str, float] = {
    MOMENTUM_FACTOR_RELATIVE_STRENGTH: 40.0,
    MOMENTUM_FACTOR_BREAKOUT: 30.0,
    MOMENTUM_FACTOR_VOLUME: 20.0,
    MOMENTUM_FACTOR_ACCELERATION: 10.0,
}

# The four momentum factor keys, for validation and iteration order.
MOMENTUM_FACTORS: tuple[str, ...] = (
    MOMENTUM_FACTOR_RELATIVE_STRENGTH,
    MOMENTUM_FACTOR_BREAKOUT,
    MOMENTUM_FACTOR_VOLUME,
    MOMENTUM_FACTOR_ACCELERATION,
)

# A mover surfaces only when its 0-100 momentum score meets or exceeds this threshold.
DEFAULT_MOMENTUM_THRESHOLD = 60.0
# Lookback windows (in daily candles) for the relative-strength-vs-BTC return and for
# the trailing breakout high/low.
DEFAULT_RS_LOOKBACK_DAYS = 30
DEFAULT_BREAKOUT_LOOKBACK_DAYS = 20

# Volume/breakout/liquidity category tunables. In-code defaults for this phase; task 07
# lifts them onto the validated YAML config surface.
# Relative volume (current vs rolling average) must reach this multiple for full
# Volume-confirmation credit; 1.0 is an average-sized candle.
DEFAULT_RELATIVE_VOLUME_MULTIPLE = 1.5
# Minimum cumulative order-book depth on the binding side for full Liquidity credit,
# measured as quote-currency (e.g. USDT) *notional* — base-asset depth × mid price — so
# the threshold is unit-consistent across coins of any unit price. A flat base-asset
# floor wrongly penalized high-unit-price coins (50 units of BTC is millions of dollars,
# 50 units of a sub-cent memecoin is a fraction of a cent); notional fixes that.
# Calibrated against the top-N sampled levels (see ``DEFAULT_ORDER_BOOK_DEPTH``): real,
# actively-traded alts carry only ~$5k-25k over the top 20 levels, so this floor excludes
# dead/synthetic books without cutting the volatile movers the scanner targets. Tunable.
DEFAULT_MIN_DEPTH = 5_000.0
# Maximum acceptable relative spread ((ask - bid) / mid) for full Liquidity credit;
# 0.005 is 0.5%.
DEFAULT_MAX_SPREAD = 0.005

# Trade-planner tunables. In-code defaults for this phase; task 07 lifts them onto the
# validated YAML config surface.
# Volatility buffer as a multiple of ATR, padded beyond the structural invalidation
# level so the stop is only hit when the setup is genuinely wrong (0.5 ATR).
DEFAULT_ATR_BUFFER = 0.5
# Target risk-to-reward the take-profit is floored by, and the ratio at which the
# Risk-to-reward category earns full credit.
DEFAULT_TARGET_RR = 2.0
# Reference timeframe whose candles/structure/ATR drive the trade plan.
DEFAULT_REFERENCE_TIMEFRAME = "4h"

# Trading-style profile. Each preset selects the analysis timeframe set (ascending);
# the leading timeframe, higher-timeframe subset, and reference (level) timeframe are
# derived from that set, so a preset is fully described by its timeframes. ``futures``
# is the default and preserves today's timeframes.
DEFAULT_PROFILE = "futures"
PROFILE_TIMEFRAMES: dict[str, list[str]] = {
    "futures": ["15m", "1h", "4h", "1d"],
    "spot": ["1d", "1w", "1M"],
}
VALID_PROFILES = frozenset(PROFILE_TIMEFRAMES)

# The higher-timeframe subset and leading timeframe of the default (futures) profile.
# Kept as Config defaults so a directly-constructed ``Config`` (bypassing
# ``load_config``) resolves the same futures profile as ``load_config(None)``.
DEFAULT_HTF_TIMEFRAMES = ["4h", "1d"]
DEFAULT_LEAD_TIMEFRAME = "1d"

# The relaxed direction rule's tunables (see ``trader.direction``): the lower higher
# timeframe filters as must-not-oppose by default, and the BTC market veto is on.
DEFAULT_REQUIRE_CONFIRMATION = False
DEFAULT_BTC_VETO = True

# Backtest defaults. Costs are net by default (both zero reproduces the gross edge);
# ``risk_per_trade`` is the fraction of running equity risked per trade on the
# fixed-fractional equity curve; ``max_holding_bars`` is off by default; ``start``/``end``
# bound the traded date range (unset = the whole available history); ``cache_dir`` is
# where fetched candles are cached on disk.
DEFAULT_BACKTEST_FEE_RATE = 0.0004
DEFAULT_BACKTEST_SLIPPAGE = 0.0005
DEFAULT_BACKTEST_RISK_PER_TRADE = 0.01
DEFAULT_BACKTEST_MAX_HOLDING_BARS: int | None = None
DEFAULT_BACKTEST_CACHE_DIR = ".cache/backtest"
# Bounded worker pool for the concurrent cold fetch. A modest default keeps the exchange's
# rate limits happy while still fetching several coins/timeframes at once.
DEFAULT_BACKTEST_MAX_WORKERS = 5
# How far back the daily cache refresh fills on a cold start, in days. Bounds the first
# (unbounded-looking) fill so it is predictable; ~2 years of history by default.
DEFAULT_HISTORY_HORIZON_DAYS = 730


class ConfigError(Exception):
    """Raised when a configuration file is malformed or contains invalid values."""


def parse_iso_date(value: str, field_name: str) -> int:
    """Parse an ISO date/datetime string into epoch milliseconds (UTC).

    Accepts ``YYYY-MM-DD`` or a full ISO datetime. A naive value is treated as UTC. An
    unparseable value raises :class:`ConfigError` so a misconfigured range fails loudly.
    """

    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(
            f"'{field_name}' must be an ISO date (e.g. 2024-01-31): {value!r}"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


@dataclass(frozen=True)
class BacktestConfig:
    """Validated backtest settings: costs, risk sizing, holding cap, range, and cache.

    ``start``/``end`` are ISO date strings (or ``None`` for the whole history); use
    :func:`parse_iso_date` to convert them to epoch milliseconds.
    """

    fee_rate: float = DEFAULT_BACKTEST_FEE_RATE
    slippage: float = DEFAULT_BACKTEST_SLIPPAGE
    risk_per_trade: float = DEFAULT_BACKTEST_RISK_PER_TRADE
    max_holding_bars: int | None = DEFAULT_BACKTEST_MAX_HOLDING_BARS
    start: str | None = None
    end: str | None = None
    cache_dir: str = DEFAULT_BACKTEST_CACHE_DIR
    max_workers: int = DEFAULT_BACKTEST_MAX_WORKERS
    history_horizon_days: int = DEFAULT_HISTORY_HORIZON_DAYS


@dataclass(frozen=True)
class ResolvedProfile:
    """The concrete timeframe selection a trading-style profile expands into.

    ``timeframes`` is the ascending analysis set; ``lead_timeframe`` is the highest
    timeframe (it decides direction); ``htf_timeframes`` is the higher-timeframe
    subset the direction rule filters on; ``reference_timeframe`` is the timeframe
    whose structure/ATR draw the trade-plan levels.
    """

    timeframes: list[str]
    htf_timeframes: list[str]
    lead_timeframe: str
    reference_timeframe: str


def resolve_profile(
    profile: str,
    *,
    timeframes: list[str] | None = None,
    htf_timeframes: list[str] | None = None,
    lead_timeframe: str | None = None,
    reference_timeframe: str | None = None,
) -> ResolvedProfile:
    """Expand a trading-style preset into a concrete :class:`ResolvedProfile`.

    ``futures`` yields ``15m/1h/4h/1d`` (higher timeframes ``4h``+``1d``, lead ``1d``,
    reference ``4h``); ``spot`` yields ``1d/1w/1M`` (higher timeframes ``1w``+``1M``,
    lead ``1M``, reference ``1w``). The leading timeframe is the highest (last) in the
    set, the higher-timeframe subset is the top two, and the reference (level)
    timeframe is the second-highest. Any explicitly supplied override wins over the
    derived preset value; when ``timeframes`` is overridden the other derived values
    follow the new set unless they too are overridden. An unknown ``profile`` raises
    :class:`ConfigError`. This function is pure: it performs no I/O.
    """

    if profile not in PROFILE_TIMEFRAMES:
        raise ConfigError(
            f"'profile' must be one of: {', '.join(sorted(PROFILE_TIMEFRAMES))}"
        )

    tfs = list(timeframes) if timeframes is not None else list(PROFILE_TIMEFRAMES[profile])
    lead = lead_timeframe if lead_timeframe is not None else tfs[-1]
    htf = list(htf_timeframes) if htf_timeframes is not None else list(tfs[-2:])
    default_ref = tfs[-2] if len(tfs) >= 2 else tfs[-1]
    ref = reference_timeframe if reference_timeframe is not None else default_ref
    return ResolvedProfile(
        timeframes=tfs,
        htf_timeframes=htf,
        lead_timeframe=lead,
        reference_timeframe=ref,
    )


@dataclass(frozen=True)
class Config:
    """Validated configuration for a single analysis run."""

    watchlist: list[str] = field(default_factory=lambda: list(DEFAULT_WATCHLIST))
    exchange: str = DEFAULT_EXCHANGE
    screener: str = DEFAULT_SCREENER
    timeframes: list[str] = field(default_factory=lambda: list(DEFAULT_TIMEFRAMES))
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    request_delay: float = DEFAULT_REQUEST_DELAY
    watch_interval: float | None = DEFAULT_WATCH_INTERVAL
    # Multi-factor scoring settings, parsed and validated from YAML. The v1 buy/sell
    # thresholds are retired in favour of the single ``quality_threshold`` below.
    category_weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_CATEGORY_WEIGHTS)
    )
    quality_threshold: float = DEFAULT_QUALITY_THRESHOLD
    relative_volume_multiple: float = DEFAULT_RELATIVE_VOLUME_MULTIPLE
    min_depth: float = DEFAULT_MIN_DEPTH
    max_spread: float = DEFAULT_MAX_SPREAD
    atr_buffer: float = DEFAULT_ATR_BUFFER
    target_rr: float = DEFAULT_TARGET_RR
    reference_timeframe: str = DEFAULT_REFERENCE_TIMEFRAME
    # Trading-style profile and the direction-rule selection it resolves into. The
    # list fields are the resolved values (explicit overrides already applied).
    profile: str = DEFAULT_PROFILE
    htf_timeframes: list[str] = field(default_factory=lambda: list(DEFAULT_HTF_TIMEFRAMES))
    lead_timeframe: str = DEFAULT_LEAD_TIMEFRAME
    require_confirmation: bool = DEFAULT_REQUIRE_CONFIRMATION
    btc_veto: bool = DEFAULT_BTC_VETO
    binance_delay: float = DEFAULT_BINANCE_DELAY
    tradingview_delay: float = DEFAULT_TRADINGVIEW_DELAY
    tradingview_batch_size: int = DEFAULT_TRADINGVIEW_BATCH_SIZE
    ohlcv_lookback: int = DEFAULT_OHLCV_LOOKBACK
    long_only: bool = DEFAULT_LONG_ONLY
    # Momentum / breakout scanner ("movers") settings — a second, independent scanner.
    momentum_weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_MOMENTUM_WEIGHTS)
    )
    momentum_threshold: float = DEFAULT_MOMENTUM_THRESHOLD
    rs_lookback_days: int = DEFAULT_RS_LOOKBACK_DAYS
    breakout_lookback_days: int = DEFAULT_BREAKOUT_LOOKBACK_DAYS
    backtest: BacktestConfig = field(default_factory=BacktestConfig)


def _require_str_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"'{field_name}' must be a non-empty list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"'{field_name}' entries must be non-empty strings")
        result.append(item)
    return result


def _require_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{field_name}' must be a non-empty string")
    return value


def _require_float(value: Any, field_name: str) -> float:
    # bool is a subclass of int; reject it explicitly so True/False can't slip through.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"'{field_name}' must be a number")
    return float(value)


def _require_int(value: Any, field_name: str) -> int:
    # bool is a subclass of int; reject it explicitly so True/False can't slip through.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"'{field_name}' must be an integer")
    return int(value)


def _require_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"'{field_name}' must be a boolean")
    return value


def _require_date_str(value: Any, field_name: str) -> str:
    # PyYAML auto-parses unquoted ISO dates into date/datetime objects; accept those (as
    # their ISO string) as well as an explicit string, then validate it parses.
    if isinstance(value, (date, datetime)):
        text = value.isoformat()
    elif isinstance(value, str) and value.strip():
        text = value
    else:
        raise ConfigError(f"'{field_name}' must be an ISO date string")
    parse_iso_date(text, field_name)  # raises ConfigError if unparseable
    return text


def _require_weights(value: Any) -> dict[str, float]:
    if not isinstance(value, dict) or not value:
        raise ConfigError("'weights' must be a non-empty mapping of timeframe -> number")
    result: dict[str, float] = {}
    for key, weight in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ConfigError("'weights' keys must be non-empty timeframe strings")
        parsed = _require_float(weight, f"weights['{key}']")
        if parsed < 0:
            raise ConfigError(f"weight for '{key}' must be non-negative")
        result[key] = parsed
    return result


def _require_category_weights(value: Any) -> dict[str, float]:
    if not isinstance(value, dict) or not value:
        raise ConfigError(
            "'category_weights' must be a non-empty mapping of category -> number"
        )
    result: dict[str, float] = {}
    for key, weight in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ConfigError("'category_weights' keys must be non-empty category strings")
        parsed = _require_float(weight, f"category_weights['{key}']")
        if parsed < 0:
            raise ConfigError(f"category weight for '{key}' must be non-negative")
        result[key] = parsed
    return result


def _require_momentum_weights(value: Any) -> dict[str, float]:
    if not isinstance(value, dict) or not value:
        raise ConfigError(
            "'momentum_weights' must be a non-empty mapping of factor -> number"
        )
    result: dict[str, float] = {}
    for key, weight in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ConfigError("'momentum_weights' keys must be non-empty factor strings")
        parsed = _require_float(weight, f"momentum_weights['{key}']")
        if parsed < 0:
            raise ConfigError(f"momentum weight for '{key}' must be non-negative")
        result[key] = parsed
    return result


def _require_backtest(value: Any) -> BacktestConfig:
    """Validate a ``backtest`` sub-mapping into a :class:`BacktestConfig`.

    Each field falls back to its documented default; unknown keys are rejected. Invalid
    values (negative fee/slippage, risk-per-trade outside ``(0, 1]``, non-positive holding
    cap, unparseable dates, or a start after the end) raise :class:`ConfigError`.
    """

    if not isinstance(value, dict):
        raise ConfigError("'backtest' must be a mapping of settings")

    unknown = set(value) - {
        "fee_rate",
        "slippage",
        "risk_per_trade",
        "max_holding_bars",
        "start",
        "end",
        "cache_dir",
        "max_workers",
        "history_horizon_days",
    }
    if unknown:
        raise ConfigError(
            f"unknown backtest configuration key(s): {', '.join(sorted(unknown))}"
        )

    fee_rate = (
        _require_float(value["fee_rate"], "fee_rate")
        if "fee_rate" in value
        else DEFAULT_BACKTEST_FEE_RATE
    )
    slippage = (
        _require_float(value["slippage"], "slippage")
        if "slippage" in value
        else DEFAULT_BACKTEST_SLIPPAGE
    )
    risk_per_trade = (
        _require_float(value["risk_per_trade"], "risk_per_trade")
        if "risk_per_trade" in value
        else DEFAULT_BACKTEST_RISK_PER_TRADE
    )
    if "max_holding_bars" in value and value["max_holding_bars"] is not None:
        max_holding_bars: int | None = _require_int(value["max_holding_bars"], "max_holding_bars")
    else:
        max_holding_bars = DEFAULT_BACKTEST_MAX_HOLDING_BARS
    start = _require_date_str(value["start"], "start") if value.get("start") is not None else None
    end = _require_date_str(value["end"], "end") if value.get("end") is not None else None
    cache_dir = (
        _require_str(value["cache_dir"], "cache_dir")
        if "cache_dir" in value
        else DEFAULT_BACKTEST_CACHE_DIR
    )
    max_workers = (
        _require_int(value["max_workers"], "max_workers")
        if "max_workers" in value
        else DEFAULT_BACKTEST_MAX_WORKERS
    )
    history_horizon_days = (
        _require_int(value["history_horizon_days"], "history_horizon_days")
        if "history_horizon_days" in value
        else DEFAULT_HISTORY_HORIZON_DAYS
    )

    if fee_rate < 0:
        raise ConfigError("backtest fee_rate must be non-negative")
    if slippage < 0:
        raise ConfigError("backtest slippage must be non-negative")
    if not 0.0 < risk_per_trade <= 1.0:
        raise ConfigError("backtest risk_per_trade must be between 0 and 1")
    if max_holding_bars is not None and max_holding_bars <= 0:
        raise ConfigError("backtest max_holding_bars must be positive when set")
    if max_workers < 1:
        raise ConfigError("backtest max_workers must be at least 1")
    if history_horizon_days <= 0:
        raise ConfigError("backtest history_horizon_days must be positive")

    start_ms = parse_iso_date(start, "start") if start is not None else None
    end_ms = parse_iso_date(end, "end") if end is not None else None
    if start_ms is not None and end_ms is not None and start_ms > end_ms:
        raise ConfigError("backtest start must not be after end")

    return BacktestConfig(
        fee_rate=fee_rate,
        slippage=slippage,
        risk_per_trade=risk_per_trade,
        max_holding_bars=max_holding_bars,
        start=start,
        end=end,
        cache_dir=cache_dir,
        max_workers=max_workers,
        history_horizon_days=history_horizon_days,
    )


def load_config(path: str | None) -> Config:
    """Load and validate a YAML config file into a :class:`Config`.

    When ``path`` is ``None`` the built-in defaults are returned. When a file is
    given, its top level must be a mapping; each present field is validated and
    each omitted field receives its documented default. Missing files, malformed
    YAML, wrong types, or out-of-range values raise :class:`ConfigError`.
    """

    if path is None:
        return Config()

    try:
        with open(path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse YAML configuration '{path}': {exc}") from exc

    if raw is None:
        # An empty file is treated as "use all defaults".
        return Config()
    if not isinstance(raw, dict):
        raise ConfigError("configuration root must be a mapping of settings")

    # ``buy_threshold``/``sell_threshold`` are intentionally absent: the v1 buy/sell
    # thresholds are retired in favour of ``quality_threshold``, so a file still setting
    # them is rejected below as an unknown key.
    unknown = set(raw) - {
        "watchlist",
        "exchange",
        "screener",
        "timeframes",
        "weights",
        "request_delay",
        "watch_interval",
        "category_weights",
        "quality_threshold",
        "relative_volume_multiple",
        "min_depth",
        "max_spread",
        "atr_buffer",
        "target_rr",
        "reference_timeframe",
        "profile",
        "htf_timeframes",
        "lead_timeframe",
        "require_confirmation",
        "btc_veto",
        "binance_delay",
        "tradingview_delay",
        "tradingview_batch_size",
        "ohlcv_lookback",
        "long_only",
        "momentum_weights",
        "momentum_threshold",
        "rs_lookback_days",
        "breakout_lookback_days",
        "backtest",
    }
    if unknown:
        raise ConfigError(f"unknown configuration key(s): {', '.join(sorted(unknown))}")

    watchlist = (
        _require_str_list(raw["watchlist"], "watchlist")
        if "watchlist" in raw
        else list(DEFAULT_WATCHLIST)
    )
    exchange = _require_str(raw["exchange"], "exchange") if "exchange" in raw else DEFAULT_EXCHANGE
    screener = _require_str(raw["screener"], "screener") if "screener" in raw else DEFAULT_SCREENER
    # Trading-style profile plus any explicit overrides. Overrides are only collected
    # when present; ``resolve_profile`` fills the rest from the preset and derives the
    # lead/htf/reference from the (possibly overridden) timeframe set.
    profile = _require_str(raw["profile"], "profile") if "profile" in raw else DEFAULT_PROFILE
    timeframes_override = (
        _require_str_list(raw["timeframes"], "timeframes") if "timeframes" in raw else None
    )
    htf_override = (
        _require_str_list(raw["htf_timeframes"], "htf_timeframes")
        if "htf_timeframes" in raw
        else None
    )
    lead_override = (
        _require_str(raw["lead_timeframe"], "lead_timeframe")
        if "lead_timeframe" in raw
        else None
    )
    reference_override = (
        _require_str(raw["reference_timeframe"], "reference_timeframe")
        if "reference_timeframe" in raw
        else None
    )
    resolved = resolve_profile(
        profile,
        timeframes=timeframes_override,
        htf_timeframes=htf_override,
        lead_timeframe=lead_override,
        reference_timeframe=reference_override,
    )
    timeframes = resolved.timeframes
    htf_timeframes = resolved.htf_timeframes
    lead_timeframe = resolved.lead_timeframe
    reference_timeframe = resolved.reference_timeframe
    require_confirmation = (
        _require_bool(raw["require_confirmation"], "require_confirmation")
        if "require_confirmation" in raw
        else DEFAULT_REQUIRE_CONFIRMATION
    )
    btc_veto = (
        _require_bool(raw["btc_veto"], "btc_veto") if "btc_veto" in raw else DEFAULT_BTC_VETO
    )

    weights = _require_weights(raw["weights"]) if "weights" in raw else dict(DEFAULT_WEIGHTS)
    request_delay = (
        _require_float(raw["request_delay"], "request_delay")
        if "request_delay" in raw
        else DEFAULT_REQUEST_DELAY
    )

    if "watch_interval" in raw and raw["watch_interval"] is not None:
        watch_interval: float | None = _require_float(raw["watch_interval"], "watch_interval")
    else:
        watch_interval = DEFAULT_WATCH_INTERVAL

    category_weights = (
        _require_category_weights(raw["category_weights"])
        if "category_weights" in raw
        else dict(DEFAULT_CATEGORY_WEIGHTS)
    )
    quality_threshold = (
        _require_float(raw["quality_threshold"], "quality_threshold")
        if "quality_threshold" in raw
        else DEFAULT_QUALITY_THRESHOLD
    )
    relative_volume_multiple = (
        _require_float(raw["relative_volume_multiple"], "relative_volume_multiple")
        if "relative_volume_multiple" in raw
        else DEFAULT_RELATIVE_VOLUME_MULTIPLE
    )
    min_depth = (
        _require_float(raw["min_depth"], "min_depth") if "min_depth" in raw else DEFAULT_MIN_DEPTH
    )
    max_spread = (
        _require_float(raw["max_spread"], "max_spread")
        if "max_spread" in raw
        else DEFAULT_MAX_SPREAD
    )
    atr_buffer = (
        _require_float(raw["atr_buffer"], "atr_buffer")
        if "atr_buffer" in raw
        else DEFAULT_ATR_BUFFER
    )
    target_rr = (
        _require_float(raw["target_rr"], "target_rr") if "target_rr" in raw else DEFAULT_TARGET_RR
    )
    binance_delay = (
        _require_float(raw["binance_delay"], "binance_delay")
        if "binance_delay" in raw
        else DEFAULT_BINANCE_DELAY
    )
    tradingview_delay = (
        _require_float(raw["tradingview_delay"], "tradingview_delay")
        if "tradingview_delay" in raw
        else DEFAULT_TRADINGVIEW_DELAY
    )
    tradingview_batch_size = (
        _require_int(raw["tradingview_batch_size"], "tradingview_batch_size")
        if "tradingview_batch_size" in raw
        else DEFAULT_TRADINGVIEW_BATCH_SIZE
    )
    ohlcv_lookback = (
        _require_int(raw["ohlcv_lookback"], "ohlcv_lookback")
        if "ohlcv_lookback" in raw
        else DEFAULT_OHLCV_LOOKBACK
    )
    long_only = (
        _require_bool(raw["long_only"], "long_only") if "long_only" in raw else DEFAULT_LONG_ONLY
    )
    momentum_weights = (
        _require_momentum_weights(raw["momentum_weights"])
        if "momentum_weights" in raw
        else dict(DEFAULT_MOMENTUM_WEIGHTS)
    )
    momentum_threshold = (
        _require_float(raw["momentum_threshold"], "momentum_threshold")
        if "momentum_threshold" in raw
        else DEFAULT_MOMENTUM_THRESHOLD
    )
    rs_lookback_days = (
        _require_int(raw["rs_lookback_days"], "rs_lookback_days")
        if "rs_lookback_days" in raw
        else DEFAULT_RS_LOOKBACK_DAYS
    )
    breakout_lookback_days = (
        _require_int(raw["breakout_lookback_days"], "breakout_lookback_days")
        if "breakout_lookback_days" in raw
        else DEFAULT_BREAKOUT_LOOKBACK_DAYS
    )
    backtest = _require_backtest(raw["backtest"]) if "backtest" in raw else BacktestConfig()

    if request_delay < 0:
        raise ConfigError("request_delay must be non-negative")
    if watch_interval is not None and watch_interval <= 0:
        raise ConfigError("watch_interval must be positive when set")
    if not 0.0 <= quality_threshold <= 100.0:
        raise ConfigError("quality_threshold must be between 0 and 100")
    if relative_volume_multiple <= 0:
        raise ConfigError("relative_volume_multiple must be positive")
    if min_depth < 0:
        raise ConfigError("min_depth must be non-negative")
    if max_spread <= 0:
        raise ConfigError("max_spread must be positive")
    if atr_buffer < 0:
        raise ConfigError("atr_buffer must be non-negative")
    if target_rr <= 0:
        raise ConfigError("target_rr must be positive")
    if binance_delay < 0:
        raise ConfigError("binance_delay must be non-negative")
    if tradingview_delay < 0:
        raise ConfigError("tradingview_delay must be non-negative")
    if tradingview_batch_size < 1:
        raise ConfigError("tradingview_batch_size must be at least 1")
    if ohlcv_lookback <= 0:
        raise ConfigError("ohlcv_lookback must be positive")
    if not 0.0 <= momentum_threshold <= 100.0:
        raise ConfigError("momentum_threshold must be between 0 and 100")
    if rs_lookback_days < 1:
        raise ConfigError("rs_lookback_days must be at least 1")
    if breakout_lookback_days < 1:
        raise ConfigError("breakout_lookback_days must be at least 1")
    if lead_timeframe not in timeframes:
        raise ConfigError("lead_timeframe must be one of the configured timeframes")
    if not set(htf_timeframes) <= set(timeframes):
        raise ConfigError("htf_timeframes must be a subset of the configured timeframes")

    return Config(
        watchlist=watchlist,
        exchange=exchange,
        screener=screener,
        timeframes=timeframes,
        weights=weights,
        request_delay=request_delay,
        watch_interval=watch_interval,
        category_weights=category_weights,
        quality_threshold=quality_threshold,
        relative_volume_multiple=relative_volume_multiple,
        min_depth=min_depth,
        max_spread=max_spread,
        atr_buffer=atr_buffer,
        target_rr=target_rr,
        reference_timeframe=reference_timeframe,
        profile=profile,
        htf_timeframes=htf_timeframes,
        lead_timeframe=lead_timeframe,
        require_confirmation=require_confirmation,
        btc_veto=btc_veto,
        binance_delay=binance_delay,
        tradingview_delay=tradingview_delay,
        tradingview_batch_size=tradingview_batch_size,
        ohlcv_lookback=ohlcv_lookback,
        long_only=long_only,
        momentum_weights=momentum_weights,
        momentum_threshold=momentum_threshold,
        rs_lookback_days=rs_lookback_days,
        breakout_lookback_days=breakout_lookback_days,
        backtest=backtest,
    )
