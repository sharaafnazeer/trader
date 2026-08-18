"""Tests for observability logging: the CLI's ``configure_logging`` helper, core-module
log emission (via ``caplog``, no network), and a CLI smoke that ``-v`` doesn't break a run.

The analysis core only *emits* records through ``logging.getLogger(__name__)``; all handler
and level wiring lives in the CLI layer's :func:`~trader.cli.configure_logging`, which these
tests exercise directly. Nothing here touches the network.
"""

from __future__ import annotations

import logging
import re

import pandas as pd
from typer.testing import CliRunner

from trader.cli import app, configure_logging
from trader.concurrent_loader import ConcurrentHistoryLoader
from trader.market_data import OHLCV_COLUMNS, Candles

_TRADER_LOGGER = "trader"


def _reset_trader_logger() -> None:
    """Drop any handlers/levels a prior test left on the shared ``"trader"`` logger."""
    logger = logging.getLogger(_TRADER_LOGGER)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(logging.NOTSET)


def test_verbosity_maps_to_levels() -> None:
    logger = logging.getLogger(_TRADER_LOGGER)
    try:
        configure_logging(0)
        assert logger.level == logging.WARNING
        configure_logging(1)
        assert logger.level == logging.INFO
        configure_logging(2)
        assert logger.level == logging.DEBUG
        # Anything above 2 stays at DEBUG (the most verbose level).
        configure_logging(5)
        assert logger.level == logging.DEBUG
    finally:
        _reset_trader_logger()


def test_stream_handler_targets_stderr_by_default() -> None:
    logger = logging.getLogger(_TRADER_LOGGER)
    try:
        configure_logging(1)
        handlers = logger.handlers
        assert len(handlers) == 1
        assert isinstance(handlers[0], logging.StreamHandler)
    finally:
        _reset_trader_logger()


def test_log_file_adds_file_handler_and_writes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    log_path = tmp_path / "trader.log"
    logger = logging.getLogger(_TRADER_LOGGER)
    try:
        configure_logging(1, str(log_path))
        # A stderr StreamHandler plus a FileHandler pointed at the given path.
        assert len(logger.handlers) == 2
        assert any(isinstance(h, logging.FileHandler) for h in logger.handlers)

        logging.getLogger("trader.example").info("hello file")
        for handler in logger.handlers:
            handler.flush()
        assert "hello file" in log_path.read_text()
    finally:
        _reset_trader_logger()


def test_configure_logging_is_idempotent() -> None:
    logger = logging.getLogger(_TRADER_LOGGER)
    try:
        configure_logging(2)
        first = len(logger.handlers)
        configure_logging(2)
        second = len(logger.handlers)
        # Repeated calls replace, never stack, handlers.
        assert first == second == 1
    finally:
        _reset_trader_logger()


class _OneBarProvider:
    """Network-free provider returning a single non-empty bar per (symbol, timeframe)."""

    def get_history(self, symbol: str, timeframe: str, start: int, end: int) -> Candles:
        frame = pd.DataFrame(
            [[float(start), 100.0, 101.0, 99.0, 100.5, 1_000.0]], columns=OHLCV_COLUMNS
        )
        return Candles(symbol=symbol, timeframe=timeframe, frame=frame)


def test_core_module_emits_info_record(caplog) -> None:  # type: ignore[no-untyped-def]
    # The loader (analysis core) emits an INFO "loading K symbols × M timeframes" line.
    with caplog.at_level(logging.INFO, logger="trader.concurrent_loader"):
        ConcurrentHistoryLoader().load(
            _OneBarProvider(), ["AAA", "BBB"], ["1h", "4h"], 0, 1_000, max_workers=2
        )

    messages = [r.getMessage() for r in caplog.records]
    assert any("loading 2 symbols × 2 timeframes" in m for m in messages)
    assert any("loaded AAA" in m for m in messages)


def test_backtest_demo_with_verbose_exits_zero() -> None:
    try:
        result = CliRunner().invoke(app, ["backtest", "--demo", "-vv"])
        assert result.exit_code == 0, result.output
    finally:
        _reset_trader_logger()


def test_scan_help_with_verbose_option_registered() -> None:
    # A wide terminal keeps the help table from wrapping the option names apart.
    result = CliRunner().invoke(app, ["scan", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "--verbose" in plain
    assert "--log-file" in plain
