"""Typed YAML configuration for the analysis core.

:func:`load_config` reads a YAML file into a validated, typed :class:`Config`.
Every field has a documented default, so an omitted field (or no file at all)
still yields a usable configuration. Malformed YAML or an out-of-range/invalid
value raises :class:`ConfigError` rather than producing a misleading result.

This module belongs to the analysis core; it does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

# Optional file of ``KEY=VALUE`` credential lines, read from the working directory. It
# fills gaps in the environment only — anything already exported wins. Loading it is what
# lets a scheduled run work without a login shell (cron reads no shell profile).
DEFAULT_ENV_FILE = ".env"

# Documented built-in defaults. Kept in sync with the pre-configuration Runner
# defaults so "no config file" behaves identically to an empty config file.
DEFAULT_EXCHANGE = "BINANCE"
DEFAULT_SCREENER = "crypto"
DEFAULT_WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
DEFAULT_TIMEFRAMES = ["15m", "1h", "4h", "1d"]
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

# Momentum / breakout scanner ("movers") settings. This is a second, independent
# scanner alongside the trend ``scan``; these keys live only on the momentum path and
# do not touch the trend scoring model. The four momentum factor keys follow a
# fractional-credit × weight pattern: each factor yields a fraction in ``[0, 1]``
# that is multiplied by its configured weight.
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

# The higher-timeframe subset and leading timeframe of the default (futures) profile.
# Kept as Config defaults so a directly-constructed ``Config`` (bypassing
# ``load_config``) resolves the same futures profile as ``load_config(None)``.
DEFAULT_HTF_TIMEFRAMES = ["4h", "1d"]
DEFAULT_LEAD_TIMEFRAME = "1d"

# The relaxed direction rule's tunables (see ``trader.direction``): the lower higher
# timeframe filters as must-not-oppose by default, and the BTC market veto is on.
DEFAULT_REQUIRE_CONFIRMATION = False
DEFAULT_BTC_VETO = True

# AI-analyst defaults. The stage is OFF by default so an existing configuration keeps
# behaving exactly as it did and no run can incur a surprise language-model bill.
# ``min_score`` and ``max_candidates`` are the two cost controls that bound how much of a
# scan is ever handed to a model: nothing below the score floor is eligible, and at most
# ``max_candidates`` of the survivors (the highest-scoring ones) are selected per run.
DEFAULT_AI_ENABLED = False
DEFAULT_AI_MIN_SCORE = 80.0
DEFAULT_AI_MAX_CANDIDATES = 5

# Slots guaranteed to each trade direction before the remaining capacity is filled by
# score. Ranking purely by engine total spends the whole budget on one side in a lopsided
# market — and a basket of same-direction positions on correlated assets is one position,
# which is the pattern behind the engine's 75% historical drawdown. Set 0 to rank purely
# by score.
DEFAULT_AI_RESERVE_PER_DIRECTION = 2

# Model provider. Only OpenAI is implemented; the analyst is written against a protocol so
# another vendor is a new class rather than a rewrite, but an unsupported value here is
# rejected loudly rather than silently falling back.
AI_PROVIDER_OPENAI = "openai"
SUPPORTED_AI_PROVIDERS = frozenset({AI_PROVIDER_OPENAI})
DEFAULT_AI_PROVIDER = AI_PROVIDER_OPENAI

# The model asked for the opinion, and the environment variable its key is read from. The
# key itself is never configuration.
DEFAULT_AI_MODEL = "gpt-5"
DEFAULT_AI_API_KEY_ENV = "OPENAI_API_KEY"

# Retries for a failed or malformed review, and the sampling temperature. Temperature is
# unset by default and simply omitted from the request when so: some reasoning models
# reject any explicit value, and a default that breaks them is worse than no default.
DEFAULT_AI_MAX_RETRIES = 2
DEFAULT_AI_TEMPERATURE: float | None = None

# Token prices in USD per million tokens, used only to estimate what a run cost. Zero
# means "not configured", which is reported as unknown rather than as free.
DEFAULT_AI_INPUT_COST_PER_MTOK = 0.0
DEFAULT_AI_OUTPUT_COST_PER_MTOK = 0.0

# Where every verdict is appended, so the analyst's contribution can be measured later
# against the engine acting alone. Sits beside the candle cache, which is already ignored
# by git.
DEFAULT_AI_DECISION_LOG = ".cache/decisions.jsonl"



# Strategy catalogue. The named setups from the trader's method — see
# ``.sdd/strategy-catalogue/rules.md`` — evaluated as a checklist and reported as four
# separate facts: trend, setup, entry status, decision. Enabled by default because it is
# now the engine's only verdict; the 0-100 score it replaced was retired with the
# indicators behind it.
DEFAULT_STRATEGIES_ENABLED = True
# How far price may sit beyond the EMA21-EMA50 retracement band, in ATR multiples and
# measured *in the trade's direction*, before the entry counts as chasing rather than
# pulling back. Two ATR is roughly where a pullback stops being one; it is a tunable
# precisely because that is a judgement until it is measured.
DEFAULT_STRATEGY_MAX_EXTENSION_ATR = 2.0
# The Stochastic RSI turn. The method wants an *event* from an *extreme*: %K crossing %D in
# the trade's direction, having come from oversold (long) or overbought (short). Both halves
# reject a different mistake — without the cross a falling knife looks like a signal because
# it is oversold all the way down; without the extreme a mid-range wobble qualifies.
DEFAULT_STRATEGY_STOCH_OVERSOLD = 0.20
DEFAULT_STRATEGY_STOCH_OVERBOUGHT = 0.80
# How recently the cross must have happened to still count as "just turned".
DEFAULT_STRATEGY_CROSS_LOOKBACK = 3
# How far back the indicator layer *detects* a crossing. Deliberately wider than any
# configured lookback: the indicators report when a cross happened and what extreme it came
# from, and the strategy decides how recent is recent enough. Holding the two in step here
# is what stops a policy of "within 10 bars" being silently capped by the detector.
CROSS_DETECTION_WINDOW = 20
# The reaction candle. The method waits for the zone to *reject* price, not merely for price
# to reach it — a bullish engulfing, a hammer, or a morning star at the zone, mirrored for a
# short. ``pattern_wick_body_ratio`` is how many times its body a wick must be to count as a
# rejection; ``reaction_lookback`` is how many recent bars the reaction may have formed on.
DEFAULT_STRATEGY_PATTERN_WICK_BODY_RATIO = 2.0
DEFAULT_STRATEGY_REACTION_LOOKBACK = 3
# The trendline. A rising line under a long, a falling one over a short, and price returned
# to it. ``trendline_min_touches`` is how many pivots make a line rather than a pair of
# points; ``trendline_min_r2`` is how well they must lie on it before it is a line the
# trader would actually draw; ``trendline_tolerance_atr`` is how near price must be to count
# as having returned to it.
DEFAULT_STRATEGY_TRENDLINE_MIN_TOUCHES = 3
DEFAULT_STRATEGY_TRENDLINE_MIN_R2 = 0.7
DEFAULT_STRATEGY_TRENDLINE_TOLERANCE_ATR = 1.5
# How near a checklist must be to complete before the coin is worth an opinion. Zero means
# only fully-ready setups are reviewed; one or two admits the near-misses, which is where a
# model is most useful — it can see a condition about to complete that the detector cannot.
DEFAULT_STRATEGY_REVIEW_WITHIN = 2
# Recent candles of the decision timeframe handed to the model, so it can read price action
# the pattern predicates do not cover. This is the largest single contributor to prompt
# size, which is why it is bounded and configurable rather than "send the frame".
DEFAULT_STRATEGY_EVIDENCE_CANDLES = 30

# --- Strategy 2, the breakout family (2A long / 2B short) -------------------------------
# A separate block from the pullback settings above because the two strategies gate on
# different things: the breakout family reads levels and volume, and treats the moving
# average stack as context rather than a condition.
#
# ``enabled`` gates the family. ``min_touches`` is what makes a level *established* — one
# swing pivot is a place price turned, several at the same price is a level participants
# watch — and ``level_tolerance_atr`` is how near two pivots must be to count as the same
# level, since a real level is a band rather than a number.
DEFAULT_BREAKOUT_ENABLED = False
DEFAULT_BREAKOUT_MIN_TOUCHES = 2
DEFAULT_BREAKOUT_LEVEL_TOLERANCE_ATR = 0.5
# What separates a break from a touch: how far beyond the level the candle must *close*,
# and how much of that candle must be body rather than the wick that merely visited.
DEFAULT_BREAKOUT_MIN_BREAK_ATR = 0.25
DEFAULT_BREAKOUT_MIN_BODY_RATIO = 0.5
# Volume expansion on the breaking bar, against the same rolling window the engine uses for
# relative volume. The method names 1.5x; whether a lower figure does as well is unmeasured,
# which is why it is a knob and not a constant.
DEFAULT_BREAKOUT_VOLUME_MULTIPLE = 1.5
# Room to the next level in the break's direction, and how far past the level price may run
# before the entry is a chase. Beyond the latter the answer is "wait for the retest", not
# "no setup" — the level is still the level.
DEFAULT_BREAKOUT_MIN_ROOM_ATR = 2.0
DEFAULT_BREAKOUT_MAX_EXTENSION_ATR = 2.0
# How many recent bars may contain the break. A break twenty bars old is history, not a
# setup; the same reasoning as the Stoch RSI cross window.
DEFAULT_BREAKOUT_BREAK_LOOKBACK = 3

# Telegram alerting. Off by default, and — like the model credential — the bot token and
# chat id are named environment variables, never values in a file. ``min_confidence`` is
# the floor for interrupting the trader; it gates only actionable verdicts, because a
# high-confidence WAIT is still a WAIT.
DEFAULT_TELEGRAM_ENABLED = False
DEFAULT_TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
DEFAULT_TELEGRAM_CHAT_ID_ENV = "TELEGRAM_CHAT_ID"
DEFAULT_TELEGRAM_MIN_CONFIDENCE = 70.0

# Configuration keys that would hold a secret verbatim. Credentials belong in the
# environment, never in a file that gets committed, so these are rejected by name with a
# pointed message rather than the generic unknown-key error.
_SECRET_LOOKING_KEYS = frozenset(
    {"api_key", "apikey", "key", "secret", "token", "password", "bot_token"}
)

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
# Worker processes for the *replay*, which is CPU-bound — distinct from ``max_workers``,
# which bounds concurrent network fetches. Zero means auto: leave two cores free. They are
# separate knobs because conflating network concurrency with CPU concurrency gets one of
# them wrong; the exchange's rate limit has nothing to do with how many cores you have.
DEFAULT_BACKTEST_REPLAY_WORKERS = 0
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
    replay_workers: int = DEFAULT_BACKTEST_REPLAY_WORKERS
    history_horizon_days: int = DEFAULT_HISTORY_HORIZON_DAYS


@dataclass(frozen=True)
class AIConfig:
    """Validated settings for the AI-analyst stage.

    ``enabled`` gates the whole stage. ``min_score`` is the engine total a surfaced setup
    must reach before it is eligible for review, and ``max_candidates`` caps how many of
    the eligible setups are selected in a single run. Together they bound the payload —
    and therefore the cost — of every run.

    This block deliberately holds no credential: the model provider's key is read from
    the environment, and a file placing one here is rejected.
    """

    enabled: bool = DEFAULT_AI_ENABLED
    min_score: float = DEFAULT_AI_MIN_SCORE
    max_candidates: int = DEFAULT_AI_MAX_CANDIDATES
    reserve_per_direction: int = DEFAULT_AI_RESERVE_PER_DIRECTION
    provider: str = DEFAULT_AI_PROVIDER
    model: str = DEFAULT_AI_MODEL
    api_key_env: str = DEFAULT_AI_API_KEY_ENV
    max_retries: int = DEFAULT_AI_MAX_RETRIES
    temperature: float | None = DEFAULT_AI_TEMPERATURE
    input_cost_per_mtok: float = DEFAULT_AI_INPUT_COST_PER_MTOK
    output_cost_per_mtok: float = DEFAULT_AI_OUTPUT_COST_PER_MTOK
    decision_log: str = DEFAULT_AI_DECISION_LOG




@dataclass(frozen=True)
class StrategyConfig:
    """Settings for the named-strategy checklist.

    ``enabled`` gates the whole catalogue. ``max_extension_atr`` is the single distance
    knob: how far beyond the EMA21-EMA50 band price may sit, in the trade's own direction,
    and still count as an entry. It is deliberately one knob rather than a separate
    "tolerance" and "limit" — two thresholds measuring the same distance would overlap and
    the trader would have to reason about which one bit.

    ``stoch_oversold`` / ``stoch_overbought`` are the extremes a momentum turn must come
    from, on the 0-1 scale the Stochastic RSI is reported on. ``cross_lookback`` is how many
    bars ago the cross may have happened and still count as a turn.

    The checklist is evaluated on the *reference* timeframe rather than a separately
    configured one, so the verdict and the trade plan always describe the same trade.
    """

    enabled: bool = DEFAULT_STRATEGIES_ENABLED
    max_extension_atr: float = DEFAULT_STRATEGY_MAX_EXTENSION_ATR
    stoch_oversold: float = DEFAULT_STRATEGY_STOCH_OVERSOLD
    stoch_overbought: float = DEFAULT_STRATEGY_STOCH_OVERBOUGHT
    cross_lookback: int = DEFAULT_STRATEGY_CROSS_LOOKBACK
    pattern_wick_body_ratio: float = DEFAULT_STRATEGY_PATTERN_WICK_BODY_RATIO
    reaction_lookback: int = DEFAULT_STRATEGY_REACTION_LOOKBACK
    trendline_min_touches: int = DEFAULT_STRATEGY_TRENDLINE_MIN_TOUCHES
    trendline_min_r2: float = DEFAULT_STRATEGY_TRENDLINE_MIN_R2
    trendline_tolerance_atr: float = DEFAULT_STRATEGY_TRENDLINE_TOLERANCE_ATR
    review_within: int = DEFAULT_STRATEGY_REVIEW_WITHIN
    evidence_candles: int = DEFAULT_STRATEGY_EVIDENCE_CANDLES


@dataclass(frozen=True)
class BreakoutConfig:
    """Settings for Strategy 2, the breakout family.

    A separate block from :class:`StrategyConfig` because the two families gate on
    different evidence, and one shared block would invite tuning a pullback knob to fix a
    breakout and vice versa.

    ``enabled`` gates the family and ships **off**: unlike the pullback checklist, this one
    has never been replayed over history, and the repository's rule is that an unmeasured
    strategy does not ship on. ``min_touches`` is what makes a level *established*.
    ``volume_multiple`` is the expansion the breaking bar must show — the method names
    1.5x, and whether less suffices is exactly what a backtest would answer.
    """

    enabled: bool = DEFAULT_BREAKOUT_ENABLED
    min_touches: int = DEFAULT_BREAKOUT_MIN_TOUCHES
    level_tolerance_atr: float = DEFAULT_BREAKOUT_LEVEL_TOLERANCE_ATR
    min_break_atr: float = DEFAULT_BREAKOUT_MIN_BREAK_ATR
    min_body_ratio: float = DEFAULT_BREAKOUT_MIN_BODY_RATIO
    volume_multiple: float = DEFAULT_BREAKOUT_VOLUME_MULTIPLE
    min_room_atr: float = DEFAULT_BREAKOUT_MIN_ROOM_ATR
    max_extension_atr: float = DEFAULT_BREAKOUT_MAX_EXTENSION_ATR
    break_lookback: int = DEFAULT_BREAKOUT_BREAK_LOOKBACK


@dataclass(frozen=True)
class TelegramConfig:
    """Validated settings for pushing verdicts to a Telegram chat.

    ``enabled`` gates delivery entirely. ``min_confidence`` is the confidence an
    actionable verdict must reach before it is worth a notification. Like
    :class:`AIConfig`, this block holds no secret: the bot token and chat id are read
    from the environment variables it names.
    """

    enabled: bool = DEFAULT_TELEGRAM_ENABLED
    bot_token_env: str = DEFAULT_TELEGRAM_BOT_TOKEN_ENV
    chat_id_env: str = DEFAULT_TELEGRAM_CHAT_ID_ENV
    min_confidence: float = DEFAULT_TELEGRAM_MIN_CONFIDENCE


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
    watch_interval: float | None = DEFAULT_WATCH_INTERVAL
    # A coin surfaces on a resolved direction, a usable plan and — when the catalogue is on
    # — a complete strategy checklist. The v1 buy/sell thresholds and the 0-100 quality score
    # that succeeded them are both retired.
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
    # AI-analyst stage; disabled by default so existing runs are untouched.
    ai: AIConfig = field(default_factory=AIConfig)
    # Telegram alerting; disabled by default so nothing is ever pushed unasked.
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    strategies: StrategyConfig = field(default_factory=StrategyConfig)
    breakout: BreakoutConfig = field(default_factory=BreakoutConfig)
    # Entry-quality engine flags; all default off so behaviour is unchanged.


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


def _require_ai(value: Any) -> AIConfig:
    """Validate an ``ai`` sub-mapping into an :class:`AIConfig`.

    Each field falls back to its documented default. A key that would hold a credential
    verbatim is rejected with a pointed message (credentials come from the environment);
    any other unrecognized key is rejected as unknown. An out-of-range score floor or a
    non-positive candidate cap raises :class:`ConfigError`.
    """

    if not isinstance(value, dict):
        raise ConfigError("'ai' must be a mapping of settings")

    known = {
        "enabled",
        "min_score",
        "max_candidates",
        "reserve_per_direction",
        "provider",
        "model",
        "api_key_env",
        "max_retries",
        "temperature",
        "input_cost_per_mtok",
        "output_cost_per_mtok",
        "decision_log",
    }
    secrets = sorted(set(value) & _SECRET_LOOKING_KEYS)
    if secrets:
        raise ConfigError(
            f"ai configuration key(s) {', '.join(secrets)} must not hold a credential; "
            "supply it through an environment variable instead"
        )
    unknown = set(value) - known
    if unknown:
        raise ConfigError(f"unknown ai configuration key(s): {', '.join(sorted(unknown))}")

    enabled = (
        _require_bool(value["enabled"], "ai.enabled")
        if "enabled" in value
        else DEFAULT_AI_ENABLED
    )
    min_score = (
        _require_float(value["min_score"], "ai.min_score")
        if "min_score" in value
        else DEFAULT_AI_MIN_SCORE
    )
    max_candidates = (
        _require_int(value["max_candidates"], "ai.max_candidates")
        if "max_candidates" in value
        else DEFAULT_AI_MAX_CANDIDATES
    )

    reserve_per_direction = (
        _require_int(value["reserve_per_direction"], "ai.reserve_per_direction")
        if "reserve_per_direction" in value
        else DEFAULT_AI_RESERVE_PER_DIRECTION
    )
    provider = (
        _require_str(value["provider"], "ai.provider")
        if "provider" in value
        else DEFAULT_AI_PROVIDER
    )
    model = _require_str(value["model"], "ai.model") if "model" in value else DEFAULT_AI_MODEL
    api_key_env = (
        _require_str(value["api_key_env"], "ai.api_key_env")
        if "api_key_env" in value
        else DEFAULT_AI_API_KEY_ENV
    )
    max_retries = (
        _require_int(value["max_retries"], "ai.max_retries")
        if "max_retries" in value
        else DEFAULT_AI_MAX_RETRIES
    )
    if "temperature" in value and value["temperature"] is not None:
        temperature: float | None = _require_float(value["temperature"], "ai.temperature")
    else:
        temperature = DEFAULT_AI_TEMPERATURE
    input_cost_per_mtok = (
        _require_float(value["input_cost_per_mtok"], "ai.input_cost_per_mtok")
        if "input_cost_per_mtok" in value
        else DEFAULT_AI_INPUT_COST_PER_MTOK
    )
    output_cost_per_mtok = (
        _require_float(value["output_cost_per_mtok"], "ai.output_cost_per_mtok")
        if "output_cost_per_mtok" in value
        else DEFAULT_AI_OUTPUT_COST_PER_MTOK
    )
    decision_log = (
        _require_str(value["decision_log"], "ai.decision_log")
        if "decision_log" in value
        else DEFAULT_AI_DECISION_LOG
    )

    if not 0.0 <= min_score <= 100.0:
        raise ConfigError("ai min_score must be between 0 and 100")
    if max_candidates < 1:
        raise ConfigError("ai max_candidates must be at least 1")
    if reserve_per_direction < 0:
        raise ConfigError("ai reserve_per_direction must be non-negative")
    if provider not in SUPPORTED_AI_PROVIDERS:
        raise ConfigError(
            f"unsupported ai provider {provider!r}; "
            f"supported: {', '.join(sorted(SUPPORTED_AI_PROVIDERS))}"
        )
    if max_retries < 0:
        raise ConfigError("ai max_retries must be non-negative")
    if temperature is not None and not 0.0 <= temperature <= 2.0:
        raise ConfigError("ai temperature must be between 0 and 2")
    if input_cost_per_mtok < 0 or output_cost_per_mtok < 0:
        raise ConfigError("ai token costs must be non-negative")

    return AIConfig(
        enabled=enabled,
        min_score=min_score,
        max_candidates=max_candidates,
        reserve_per_direction=reserve_per_direction,
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        max_retries=max_retries,
        temperature=temperature,
        input_cost_per_mtok=input_cost_per_mtok,
        output_cost_per_mtok=output_cost_per_mtok,
        decision_log=decision_log,
    )


def parse_env_file(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines from an environment file into a mapping.

    Deliberately small and dependency-free. Blank lines and ``#`` comment lines are
    skipped, a leading ``export`` is tolerated so the same file can also be ``source``\\ d
    from a shell, and surrounding single or double quotes are stripped.

    Inline comments are **not** stripped from unquoted values: an API key or bot token can
    legitimately contain ``#``, and silently truncating a credential at one would produce
    a baffling authentication failure. Put the value in quotes if you want a trailing
    comment on the same line.

    Malformed lines (no ``=``) are ignored rather than raising, so one stray line cannot
    stop a run from starting. Pure: parses text, touches no process state.
    """

    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(
    path: str | Path = DEFAULT_ENV_FILE, *, environ: MutableMapping[str, str] | None = None
) -> tuple[str, ...]:
    """Load an environment file into the process environment; return the names it set.

    **An already-set variable always wins.** An explicit ``export`` in the shell, or a
    value injected by a scheduler, is a deliberate act and must not be silently overridden
    by a file someone forgot about. The file fills gaps, it does not take over.

    A missing file is a no-op, so this can be called unconditionally. Unreadable files are
    reported through the returned empty tuple rather than raising — a credential file you
    cannot read produces a clear "variable is not set" error a moment later, which is more
    useful than a stack trace here.
    """

    target = os.environ if environ is None else environ
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, PermissionError):
        return ()

    loaded: list[str] = []
    for key, value in parse_env_file(text).items():
        if target.get(key):
            continue
        target[key] = value
        loaded.append(key)
    return tuple(loaded)



def _require_breakout(value: Any) -> BreakoutConfig:
    """Validate a ``breakout`` sub-mapping, rejecting unknown keys by name.

    Every bound below rejects a value that would make a condition unfalsifiable rather than
    merely strict — a check that cannot fail is worse than an absent one, because it reads
    as evidence on the row.
    """

    if not isinstance(value, dict):
        raise ConfigError("'breakout' must be a mapping of settings")

    unknown = set(value) - {
        "enabled",
        "min_touches",
        "level_tolerance_atr",
        "min_break_atr",
        "min_body_ratio",
        "volume_multiple",
        "min_room_atr",
        "max_extension_atr",
        "break_lookback",
    }
    if unknown:
        raise ConfigError(
            f"unknown breakout configuration key(s): {', '.join(sorted(unknown))}"
        )

    enabled = _require_bool(value["enabled"], "breakout.enabled") if "enabled" in value else (
        DEFAULT_BREAKOUT_ENABLED
    )
    min_touches = (
        _require_int(value["min_touches"], "breakout.min_touches")
        if "min_touches" in value
        else DEFAULT_BREAKOUT_MIN_TOUCHES
    )
    # One touch is not a level — it is a place price turned once, which is the whole thing
    # the condition exists to exclude.
    if min_touches < 2:
        raise ConfigError("breakout.min_touches must be at least 2")

    level_tolerance_atr = (
        _require_float(value["level_tolerance_atr"], "breakout.level_tolerance_atr")
        if "level_tolerance_atr" in value
        else DEFAULT_BREAKOUT_LEVEL_TOLERANCE_ATR
    )
    if not 0.0 < level_tolerance_atr <= 5.0:
        raise ConfigError(
            "breakout.level_tolerance_atr must be greater than 0 and at most 5"
        )

    min_break_atr = (
        _require_float(value["min_break_atr"], "breakout.min_break_atr")
        if "min_break_atr" in value
        else DEFAULT_BREAKOUT_MIN_BREAK_ATR
    )
    if not 0.0 < min_break_atr <= 20.0:
        raise ConfigError("breakout.min_break_atr must be greater than 0 and at most 20")

    min_body_ratio = (
        _require_float(value["min_body_ratio"], "breakout.min_body_ratio")
        if "min_body_ratio" in value
        else DEFAULT_BREAKOUT_MIN_BODY_RATIO
    )
    # A ratio is a fraction of the candle's range. Zero admits a pure wick, which is the
    # case the condition exists to refuse; one demands a candle with no wick at all.
    if not 0.0 < min_body_ratio < 1.0:
        raise ConfigError("breakout.min_body_ratio must be between 0 and 1 (exclusive)")

    volume_multiple = (
        _require_float(value["volume_multiple"], "breakout.volume_multiple")
        if "volume_multiple" in value
        else DEFAULT_BREAKOUT_VOLUME_MULTIPLE
    )
    # Below 1.0 the condition passes on *contracting* volume, which inverts its meaning.
    if not 1.0 <= volume_multiple <= 20.0:
        raise ConfigError("breakout.volume_multiple must be between 1 and 20")

    min_room_atr = (
        _require_float(value["min_room_atr"], "breakout.min_room_atr")
        if "min_room_atr" in value
        else DEFAULT_BREAKOUT_MIN_ROOM_ATR
    )
    if not 0.0 < min_room_atr <= 50.0:
        raise ConfigError("breakout.min_room_atr must be greater than 0 and at most 50")

    max_extension_atr = (
        _require_float(value["max_extension_atr"], "breakout.max_extension_atr")
        if "max_extension_atr" in value
        else DEFAULT_BREAKOUT_MAX_EXTENSION_ATR
    )
    if not 0.0 < max_extension_atr <= 20.0:
        raise ConfigError("breakout.max_extension_atr must be greater than 0 and at most 20")

    break_lookback = (
        _require_int(value["break_lookback"], "breakout.break_lookback")
        if "break_lookback" in value
        else DEFAULT_BREAKOUT_BREAK_LOOKBACK
    )
    if not 1 <= break_lookback <= 50:
        raise ConfigError("breakout.break_lookback must be between 1 and 50")

    # The two distance knobs measure the same axis, so a break floor above the chase limit
    # would define a window no price can occupy: every real break would read both "too
    # small to count" and "already too far".
    if min_break_atr > max_extension_atr:
        raise ConfigError(
            "breakout.min_break_atr must not exceed breakout.max_extension_atr"
        )

    return BreakoutConfig(
        enabled=enabled,
        min_touches=min_touches,
        level_tolerance_atr=level_tolerance_atr,
        min_break_atr=min_break_atr,
        min_body_ratio=min_body_ratio,
        volume_multiple=volume_multiple,
        min_room_atr=min_room_atr,
        max_extension_atr=max_extension_atr,
        break_lookback=break_lookback,
    )


def _require_strategies(value: Any) -> StrategyConfig:
    """Validate a ``strategies`` sub-mapping, rejecting unknown keys by name."""

    if not isinstance(value, dict):
        raise ConfigError("'strategies' must be a mapping of settings")

    unknown = set(value) - {
        "enabled",
        "max_extension_atr",
        "stoch_oversold",
        "stoch_overbought",
        "cross_lookback",
        "pattern_wick_body_ratio",
        "reaction_lookback",
        "trendline_min_touches",
        "trendline_min_r2",
        "trendline_tolerance_atr",
        "review_within",
        "evidence_candles",
    }
    if unknown:
        raise ConfigError(
            f"unknown strategies configuration key(s): {', '.join(sorted(unknown))}"
        )

    max_extension_atr = (
        _require_float(value["max_extension_atr"], "strategies.max_extension_atr")
        if "max_extension_atr" in value
        else DEFAULT_STRATEGY_MAX_EXTENSION_ATR
    )
    # Zero would call every setup extended and block the whole watchlist; twenty ATR beyond
    # the band is a distance nothing reaches, making the condition unfalsifiable. Both ends
    # are rejected rather than silently producing a check that cannot fail either way.
    if not 0.0 < max_extension_atr <= 20.0:
        raise ConfigError("strategies.max_extension_atr must be greater than 0 and at most 20")

    stoch_oversold = (
        _require_float(value["stoch_oversold"], "strategies.stoch_oversold")
        if "stoch_oversold" in value
        else DEFAULT_STRATEGY_STOCH_OVERSOLD
    )
    stoch_overbought = (
        _require_float(value["stoch_overbought"], "strategies.stoch_overbought")
        if "stoch_overbought" in value
        else DEFAULT_STRATEGY_STOCH_OVERBOUGHT
    )
    cross_lookback = (
        _require_int(value["cross_lookback"], "strategies.cross_lookback")
        if "cross_lookback" in value
        else DEFAULT_STRATEGY_CROSS_LOOKBACK
    )
    # The Stochastic RSI is reported on a 0-1 scale, so a threshold outside it can never be
    # reached and the condition would be unfalsifiable in one direction.
    if not 0.0 < stoch_oversold < 1.0:
        raise ConfigError("strategies.stoch_oversold must be between 0 and 1 (exclusive)")
    if not 0.0 < stoch_overbought < 1.0:
        raise ConfigError("strategies.stoch_overbought must be between 0 and 1 (exclusive)")
    if stoch_oversold >= stoch_overbought:
        raise ConfigError(
            "strategies.stoch_oversold must be below strategies.stoch_overbought"
        )
    # A lookback of zero admits nothing; one wider than the detection window would be
    # silently capped, which is worse than being told.
    if not 1 <= cross_lookback <= CROSS_DETECTION_WINDOW:
        raise ConfigError(
            f"strategies.cross_lookback must be between 1 and {CROSS_DETECTION_WINDOW}"
        )

    wick_body_ratio = (
        _require_float(value["pattern_wick_body_ratio"], "strategies.pattern_wick_body_ratio")
        if "pattern_wick_body_ratio" in value
        else DEFAULT_STRATEGY_PATTERN_WICK_BODY_RATIO
    )
    reaction_lookback = (
        _require_int(value["reaction_lookback"], "strategies.reaction_lookback")
        if "reaction_lookback" in value
        else DEFAULT_STRATEGY_REACTION_LOOKBACK
    )
    # A ratio at or below one makes every candle with any wick a rejection; a very large one
    # can never be met. A lookback of zero admits nothing.
    if not 1.0 < wick_body_ratio <= 20.0:
        raise ConfigError(
            "strategies.pattern_wick_body_ratio must be greater than 1 and at most 20"
        )
    if not 1 <= reaction_lookback <= 20:
        raise ConfigError("strategies.reaction_lookback must be between 1 and 20")

    trendline_min_touches = (
        _require_int(value["trendline_min_touches"], "strategies.trendline_min_touches")
        if "trendline_min_touches" in value
        else DEFAULT_STRATEGY_TRENDLINE_MIN_TOUCHES
    )
    trendline_min_r2 = (
        _require_float(value["trendline_min_r2"], "strategies.trendline_min_r2")
        if "trendline_min_r2" in value
        else DEFAULT_STRATEGY_TRENDLINE_MIN_R2
    )
    trendline_tolerance_atr = (
        _require_float(
            value["trendline_tolerance_atr"], "strategies.trendline_tolerance_atr"
        )
        if "trendline_tolerance_atr" in value
        else DEFAULT_STRATEGY_TRENDLINE_TOLERANCE_ATR
    )
    # Two points always fit a line perfectly, so they prove nothing; twenty pivots is more
    # history than a current trendline should span. A fit quality outside 0-1 is not one.
    if not 3 <= trendline_min_touches <= 20:
        raise ConfigError("strategies.trendline_min_touches must be between 3 and 20")
    if not 0.0 <= trendline_min_r2 <= 1.0:
        raise ConfigError("strategies.trendline_min_r2 must be between 0 and 1")
    if not 0.0 < trendline_tolerance_atr <= 20.0:
        raise ConfigError(
            "strategies.trendline_tolerance_atr must be greater than 0 and at most 20"
        )

    review_within = (
        _require_int(value["review_within"], "strategies.review_within")
        if "review_within" in value
        else DEFAULT_STRATEGY_REVIEW_WITHIN
    )
    evidence_candles = (
        _require_int(value["evidence_candles"], "strategies.evidence_candles")
        if "evidence_candles" in value
        else DEFAULT_STRATEGY_EVIDENCE_CANDLES
    )
    # Admitting a coin more than a few conditions short would send the whole watchlist to
    # the model; a negative floor admits nothing at all. Candle counts are bounded because
    # the prompt is paid for by the token.
    if not 0 <= review_within <= 7:
        raise ConfigError("strategies.review_within must be between 0 and 7")
    if not 0 <= evidence_candles <= 200:
        raise ConfigError("strategies.evidence_candles must be between 0 and 200")

    return StrategyConfig(
        enabled=(
            _require_bool(value["enabled"], "strategies.enabled")
            if "enabled" in value
            else DEFAULT_STRATEGIES_ENABLED
        ),
        max_extension_atr=max_extension_atr,
        stoch_oversold=stoch_oversold,
        stoch_overbought=stoch_overbought,
        cross_lookback=cross_lookback,
        pattern_wick_body_ratio=wick_body_ratio,
        reaction_lookback=reaction_lookback,
        trendline_min_touches=trendline_min_touches,
        trendline_min_r2=trendline_min_r2,
        trendline_tolerance_atr=trendline_tolerance_atr,
        review_within=review_within,
        evidence_candles=evidence_candles,
    )


def _require_telegram(value: Any) -> TelegramConfig:
    """Validate a ``telegram`` sub-mapping into a :class:`TelegramConfig`.

    Mirrors :func:`_require_ai`: unknown keys are rejected by name, and a key that would
    hold a credential verbatim is rejected with a pointed message.
    """

    if not isinstance(value, dict):
        raise ConfigError("'telegram' must be a mapping of settings")

    known = {"enabled", "bot_token_env", "chat_id_env", "min_confidence"}
    secrets = sorted(set(value) & _SECRET_LOOKING_KEYS)
    if secrets:
        raise ConfigError(
            f"telegram configuration key(s) {', '.join(secrets)} must not hold a "
            "credential; supply it through an environment variable instead"
        )
    unknown = set(value) - known
    if unknown:
        raise ConfigError(
            f"unknown telegram configuration key(s): {', '.join(sorted(unknown))}"
        )

    enabled = (
        _require_bool(value["enabled"], "telegram.enabled")
        if "enabled" in value
        else DEFAULT_TELEGRAM_ENABLED
    )
    bot_token_env = (
        _require_str(value["bot_token_env"], "telegram.bot_token_env")
        if "bot_token_env" in value
        else DEFAULT_TELEGRAM_BOT_TOKEN_ENV
    )
    chat_id_env = (
        _require_str(value["chat_id_env"], "telegram.chat_id_env")
        if "chat_id_env" in value
        else DEFAULT_TELEGRAM_CHAT_ID_ENV
    )
    min_confidence = (
        _require_float(value["min_confidence"], "telegram.min_confidence")
        if "min_confidence" in value
        else DEFAULT_TELEGRAM_MIN_CONFIDENCE
    )

    if not 0.0 <= min_confidence <= 100.0:
        raise ConfigError("telegram min_confidence must be between 0 and 100")

    return TelegramConfig(
        enabled=enabled,
        bot_token_env=bot_token_env,
        chat_id_env=chat_id_env,
        min_confidence=min_confidence,
    )


def resolve_telegram_credentials(
    telegram: TelegramConfig, *, getenv: Callable[[str], str | None] = os.environ.get
) -> tuple[str, str]:
    """Read the bot token and chat id from the environment, naming whichever is missing.

    Both are checked before either is returned, and — like the model credential — this
    runs after any command-line override resolves, never at file-load time.
    """

    missing = [
        name
        for name in (telegram.bot_token_env, telegram.chat_id_env)
        if not (getenv(name) or "").strip()
    ]
    if missing:
        raise ConfigError(
            f"Telegram alerts are enabled but ${', $'.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} not set; "
            "export the variable(s) or disable alerts"
        )

    token = getenv(telegram.bot_token_env) or ""
    chat_id = getenv(telegram.chat_id_env) or ""
    return token.strip(), chat_id.strip()


def resolve_ai_credential(
    ai: AIConfig, *, getenv: Callable[[str], str | None] = os.environ.get
) -> str:
    """Read the analyst's API key from the environment, or fail naming the variable.

    Kept separate from :func:`load_config` on purpose. Loading a file must not depend on
    the environment, and the check has to run *after* any command-line override resolves —
    otherwise enabling the analyst for a single run against a config where it is disabled
    would skip validation entirely and fail much later, mid-run.

    ``getenv`` is injectable so tests never read a real credential.
    """

    key = getenv(ai.api_key_env)
    if not key or not key.strip():
        raise ConfigError(
            f"the AI analyst is enabled but ${ai.api_key_env} is not set; "
            f"export it or disable the analyst"
        )
    return key


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
        "replay_workers",
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
    replay_workers = (
        _require_int(value["replay_workers"], "replay_workers")
        if "replay_workers" in value
        else DEFAULT_BACKTEST_REPLAY_WORKERS
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
    if replay_workers < 0:
        raise ConfigError("backtest replay_workers must be zero (auto) or positive")
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
        replay_workers=replay_workers,
        history_horizon_days=history_horizon_days,
    )


# Settings retired when the indicator set was closed to the trader's method on 2026-08-20,
# mapped to what a reader should do instead. These get a pointed message rather than the
# generic unknown-key error, because "unknown key: quality_threshold" reads like a typo when
# the setting genuinely existed and was deliberately removed.
_RETIRED_KEYS: dict[str, str] = {
    "weights": (
        "the per-timeframe weights belonged to the v1 screener, which ranked coins on "
        "TradingView's rating alone; the scanner that replaced it derives its own indicators"
    ),
    "request_delay": (
        "the v1 single-source throttle; use binance_delay and tradingview_delay, which "
        "space the two sources independently"
    ),
    "quality_threshold": (
        "the 0-100 quality score was retired with the indicators behind it; a coin now "
        "surfaces when it resolves a direction and produces a plan"
    ),
    "category_weights": "the eight scored categories were retired along with the score",
    "relative_volume_multiple": (
        "it fed the retired volume scoring category; relative volume is still computed and "
        "reported, but nothing weights it"
    ),
    "entry": (
        "the entry-quality flags operated on the removed RSI and EMA20; the entry timing "
        "they gated is handled by the strategy checklist"
    ),
}


def _reject_retired(raw: dict[str, Any]) -> None:
    """Fail with an explanation when a file still names a retired setting."""

    for key, replacement in _RETIRED_KEYS.items():
        if key in raw:
            raise ConfigError(f"'{key}' was removed: {replacement}")


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

    # Retired settings are named explicitly so their removal is explained rather than
    # reported as a typo; anything else unrecognised falls through to the generic error.
    _reject_retired(raw)

    unknown = set(raw) - {
        "watchlist",
        "exchange",
        "screener",
        "timeframes",
        "watch_interval",
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
        "ai",
        "telegram",
        "strategies",
        "breakout",
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


    if "watch_interval" in raw and raw["watch_interval"] is not None:
        watch_interval: float | None = _require_float(raw["watch_interval"], "watch_interval")
    else:
        watch_interval = DEFAULT_WATCH_INTERVAL

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
    ai = _require_ai(raw["ai"]) if "ai" in raw else AIConfig()
    telegram = _require_telegram(raw["telegram"]) if "telegram" in raw else TelegramConfig()
    strategies = (
        _require_strategies(raw["strategies"]) if "strategies" in raw else StrategyConfig()
    )
    breakout = (
        _require_breakout(raw["breakout"]) if "breakout" in raw else BreakoutConfig()
    )

    if watch_interval is not None and watch_interval <= 0:
        raise ConfigError("watch_interval must be positive when set")
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
        watch_interval=watch_interval,
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
        ai=ai,
        telegram=telegram,
        strategies=strategies,
        breakout=breakout,
    )
