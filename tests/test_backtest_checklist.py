"""The checklist gates the backtest exactly as it gates the live scan.

This is the assertion the whole measurement rests on. If the backtest surfaced on a looser
rule than the scanner — "direction plus a plan", say — then every number it produced would
describe a strategy nobody runs, and the measurement would be worse than none: a confident
figure attached to the wrong thing.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from trader.backtester import evaluate_at, market_context_at
from trader.config import Config, StrategyConfig
from trader.direction import MarketContext
from trader.market_data import Candles
from trader.replay import Replay

_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
_STEP = {"1h": 3_600_000, "4h": 14_400_000}


def _frames(rows: int = 400) -> dict[str, Candles]:
    """A steadily rising series — clean trend, no pullback, so no entry."""

    out: dict[str, Candles] = {}
    for tf, step in _STEP.items():
        scale = _STEP["4h"] // step
        n = rows * scale
        data = []
        base = 100.0
        for i in range(n):
            base += 0.15 / scale
            close = base + 2.0 * {0: 0.0, 1: 0.5, 2: 0.9, 3: 0.5, 4: 0.0, 5: -0.35}[i % 6]
            data.append([i * step, close, close + 0.4, close - 0.4, close, 1_000.0 + i])
        out[tf] = Candles(
            symbol="AAA", timeframe=tf, frame=pd.DataFrame(data, columns=_COLUMNS)
        )
    return out


def _config(*, catalogue: bool) -> Config:
    return Config(
        watchlist=["AAA"],
        timeframes=["1h", "4h"],
        reference_timeframe="4h",
        lead_timeframe="4h",
        htf_timeframes=["4h"],
        btc_veto=False,
        strategies=StrategyConfig(enabled=catalogue),
    )


def _at(config: Config):  # type: ignore[no-untyped-def]
    frames = _frames()
    closes = Replay(frames, "4h", max_bars=config.ohlcv_lookback).reference_closes()
    return evaluate_at(frames, closes[-1], MarketContext(), config)


def test_a_moment_failing_the_checklist_does_not_surface() -> None:
    """A clean uptrend with no pullback: the trend is there, the entry is not."""

    evaluation = _at(_config(catalogue=True))

    assert evaluation.plan is not None  # enterable under the old rule
    assert evaluation.verdict is not None
    assert not evaluation.verdict.is_ready
    assert evaluation.surfaced is False


def test_the_same_moment_surfaces_with_the_catalogue_off() -> None:
    """Which is what proves the checklist is doing the gating, not something else."""

    evaluation = _at(_config(catalogue=False))

    assert evaluation.verdict is None
    assert evaluation.surfaced is True


def test_the_backtest_verdict_matches_what_the_live_evaluator_produces() -> None:
    """Both paths call one evaluator; a divergence would make any measurement a lie."""

    from trader.indicators import compute_features
    from trader.patterns import detect as detect_patterns
    from trader.strategy import evaluate as evaluate_strategy
    from trader.structure import analyze as analyze_structure
    from trader.trendline import fit as fit_trendline

    config = _config(catalogue=True)
    frames = _frames()
    replay = Replay(frames, "4h", max_bars=config.ohlcv_lookback)
    ts = replay.reference_closes()[-1]
    sliced = replay.slice_at(ts)

    from_backtest = evaluate_at(frames, ts, MarketContext(), config)
    assert from_backtest.verdict is not None

    ref = sliced["4h"]
    features = compute_features(ref)
    structure = analyze_structure(ref)
    pivots = list(zip(structure.swing_low_indices, structure.swing_lows, strict=False))
    direct = evaluate_strategy(
        "AAA",
        from_backtest.direction,
        features,
        structure,
        ref.latest_close,
        patterns=detect_patterns(ref, bars=config.strategies.reaction_lookback),
        trendline=fit_trendline(pivots, len(ref.frame) - 1),
    )

    assert direct.conditions_met == from_backtest.verdict.conditions_met
    assert direct.entry_status is from_backtest.verdict.entry_status


def test_the_market_context_cache_returns_the_uncached_value() -> None:
    """Memoisation, not approximation: the cached answer is the computed one."""

    from trader.backtester import MarketContexts

    config = _config(catalogue=True)
    btc = _frames()
    closes = Replay(btc, "4h", max_bars=config.ohlcv_lookback).reference_closes()
    contexts = MarketContexts(btc, config)

    for ts in closes[-5:]:
        assert contexts.at(ts) == market_context_at(btc, ts, config)
        # Asked twice, same answer — the cache is not mutating anything.
        assert contexts.at(ts) == market_context_at(btc, ts, config)


def test_relaxing_a_threshold_admits_a_moment_the_checklist_refused() -> None:
    """The gate is the checklist's thresholds, not an incidental failure elsewhere."""

    strict = _at(_config(catalogue=True))
    loose_config = replace(
        _config(catalogue=True),
        strategies=StrategyConfig(
            max_extension_atr=20.0,
            trendline_tolerance_atr=20.0,
            stoch_oversold=0.99,
            stoch_overbought=0.01,
            cross_lookback=20,
        ),
    )
    loose = _at(loose_config)

    assert strict.verdict is not None and loose.verdict is not None
    assert loose.verdict.conditions_met > strict.verdict.conditions_met
