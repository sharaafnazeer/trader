"""Tests for the optional ``telegram`` configuration block.

Like the ``ai`` block: off by default, and holding no credential.
"""

from __future__ import annotations

import pytest

from trader.config import (
    DEFAULT_TELEGRAM_BOT_TOKEN_ENV,
    DEFAULT_TELEGRAM_CHAT_ID_ENV,
    DEFAULT_TELEGRAM_MIN_CONFIDENCE,
    Config,
    ConfigError,
    load_config,
)


def _write(tmp_path, body: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_alerts_default_to_disabled_when_the_block_is_absent(tmp_path) -> None:
    config = load_config(_write(tmp_path, "watchlist: [BTCUSDT]\n"))

    assert config.telegram.enabled is False
    assert config.telegram.bot_token_env == DEFAULT_TELEGRAM_BOT_TOKEN_ENV
    assert config.telegram.chat_id_env == DEFAULT_TELEGRAM_CHAT_ID_ENV
    assert config.telegram.min_confidence == DEFAULT_TELEGRAM_MIN_CONFIDENCE


def test_alerts_default_to_disabled_with_no_config_file_at_all() -> None:
    assert load_config(None).telegram.enabled is False
    assert Config().telegram.enabled is False


def test_the_block_is_parsed(tmp_path) -> None:
    config = load_config(
        _write(
            tmp_path,
            "telegram:\n"
            "  enabled: true\n"
            "  bot_token_env: MY_BOT\n"
            "  chat_id_env: MY_CHAT\n"
            "  min_confidence: 85\n",
        )
    )

    assert config.telegram.enabled is True
    assert config.telegram.bot_token_env == "MY_BOT"
    assert config.telegram.chat_id_env == "MY_CHAT"
    assert config.telegram.min_confidence == pytest.approx(85.0)


def test_a_partial_block_keeps_the_remaining_defaults(tmp_path) -> None:
    config = load_config(_write(tmp_path, "telegram:\n  enabled: true\n"))

    assert config.telegram.enabled is True
    assert config.telegram.bot_token_env == DEFAULT_TELEGRAM_BOT_TOKEN_ENV


def test_an_unknown_key_is_rejected_by_name(tmp_path) -> None:
    path = _write(tmp_path, "telegram:\n  enabled: true\n  chat: 12345\n")

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    assert "chat" in str(excinfo.value)


@pytest.mark.parametrize("key", ["bot_token", "token", "api_key", "secret"])
def test_a_literal_credential_is_rejected_by_name(tmp_path, key: str) -> None:
    path = _write(tmp_path, f"telegram:\n  enabled: true\n  {key}: 123456:AA-real-token\n")

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    message = str(excinfo.value)
    assert key in message
    assert "environment variable" in message


def test_the_block_must_be_a_mapping(tmp_path) -> None:
    with pytest.raises(ConfigError, match="'telegram' must be a mapping"):
        load_config(_write(tmp_path, "telegram: yes\n"))


@pytest.mark.parametrize("value", ["-1", "101"])
def test_min_confidence_outside_zero_to_hundred_is_rejected(tmp_path, value: str) -> None:
    with pytest.raises(ConfigError, match="min_confidence must be between 0 and 100"):
        load_config(_write(tmp_path, f"telegram:\n  min_confidence: {value}\n"))


def test_the_example_config_still_loads() -> None:
    assert load_config("config.example.yaml").telegram.enabled is False
