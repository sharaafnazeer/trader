"""Tests for the optional ``ai`` configuration block.

The block is off by default and holds no credential — both are load-bearing, so both are
pinned here alongside the usual validation.
"""

from __future__ import annotations

import pytest

from trader.config import (
    DEFAULT_AI_API_KEY_ENV,
    DEFAULT_AI_DECISION_LOG,
    DEFAULT_AI_MAX_CANDIDATES,
    DEFAULT_AI_MAX_RETRIES,
    DEFAULT_AI_MIN_SCORE,
    DEFAULT_AI_MODEL,
    DEFAULT_AI_RESERVE_PER_DIRECTION,
    Config,
    ConfigError,
    load_config,
)


def _write(tmp_path, body: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_ai_defaults_to_disabled_when_the_block_is_absent(tmp_path) -> None:
    config = load_config(_write(tmp_path, "watchlist: [BTCUSDT]\n"))

    assert config.ai.enabled is False
    assert config.ai.min_score == DEFAULT_AI_MIN_SCORE
    assert config.ai.max_candidates == DEFAULT_AI_MAX_CANDIDATES


def test_ai_defaults_to_disabled_with_no_config_file_at_all() -> None:
    assert load_config(None).ai.enabled is False
    assert Config().ai.enabled is False


def test_ai_block_is_parsed(tmp_path) -> None:
    config = load_config(
        _write(
            tmp_path,
            "ai:\n  enabled: true\n  min_score: 65\n  max_candidates: 3\n",
        )
    )

    assert config.ai.enabled is True
    assert config.ai.min_score == pytest.approx(65.0)
    assert config.ai.max_candidates == 3


def test_partial_ai_block_keeps_the_remaining_defaults(tmp_path) -> None:
    config = load_config(_write(tmp_path, "ai:\n  enabled: true\n"))

    assert config.ai.enabled is True
    assert config.ai.min_score == DEFAULT_AI_MIN_SCORE
    assert config.ai.max_candidates == DEFAULT_AI_MAX_CANDIDATES


def test_unknown_key_inside_the_ai_block_is_rejected_by_name(tmp_path) -> None:
    path = _write(tmp_path, "ai:\n  enabled: true\n  temprature: 0.2\n")

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    assert "temprature" in str(excinfo.value)


@pytest.mark.parametrize("key", ["api_key", "apikey", "key", "secret", "token", "password"])
def test_a_literal_credential_in_the_ai_block_is_rejected_by_name(tmp_path, key: str) -> None:
    path = _write(tmp_path, f"ai:\n  enabled: true\n  {key}: sk-not-a-real-key\n")

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    message = str(excinfo.value)
    assert key in message
    assert "environment variable" in message


def test_ai_block_must_be_a_mapping(tmp_path) -> None:
    with pytest.raises(ConfigError, match="'ai' must be a mapping"):
        load_config(_write(tmp_path, "ai: true\n"))


@pytest.mark.parametrize("value", ["-1", "101"])
def test_min_score_outside_zero_to_hundred_is_rejected(tmp_path, value: str) -> None:
    with pytest.raises(ConfigError, match="min_score must be between 0 and 100"):
        load_config(_write(tmp_path, f"ai:\n  min_score: {value}\n"))


def test_max_candidates_below_one_is_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError, match="max_candidates must be at least 1"):
        load_config(_write(tmp_path, "ai:\n  max_candidates: 0\n"))


def test_non_boolean_enabled_is_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError, match="ai.enabled"):
        load_config(_write(tmp_path, "ai:\n  enabled: yes please\n"))


def test_model_settings_default_when_absent(tmp_path) -> None:
    config = load_config(_write(tmp_path, "ai:\n  enabled: true\n"))

    assert config.ai.provider == "openai"
    assert config.ai.model == DEFAULT_AI_MODEL
    assert config.ai.api_key_env == DEFAULT_AI_API_KEY_ENV
    assert config.ai.max_retries == DEFAULT_AI_MAX_RETRIES
    # Unset by default so the request omits it: some reasoning models reject any value.
    assert config.ai.temperature is None
    assert config.ai.input_cost_per_mtok == 0.0
    assert config.ai.output_cost_per_mtok == 0.0
    assert config.ai.decision_log == DEFAULT_AI_DECISION_LOG


def test_model_settings_are_parsed(tmp_path) -> None:
    config = load_config(
        _write(
            tmp_path,
            "ai:\n"
            "  enabled: true\n"
            "  provider: openai\n"
            "  model: gpt-5-mini\n"
            "  api_key_env: MY_OPENAI_KEY\n"
            "  max_retries: 4\n"
            "  temperature: 0.3\n"
            "  input_cost_per_mtok: 1.25\n"
            "  output_cost_per_mtok: 10\n",
        )
    )

    assert config.ai.model == "gpt-5-mini"
    assert config.ai.api_key_env == "MY_OPENAI_KEY"
    assert config.ai.max_retries == 4
    assert config.ai.temperature == pytest.approx(0.3)
    assert config.ai.input_cost_per_mtok == pytest.approx(1.25)
    assert config.ai.output_cost_per_mtok == pytest.approx(10.0)


def test_reserve_per_direction_defaults_and_parses(tmp_path) -> None:
    default = load_config(_write(tmp_path, "ai:\n  enabled: true\n"))
    explicit = load_config(_write(tmp_path, "ai:\n  reserve_per_direction: 4\n"))

    assert default.ai.reserve_per_direction == DEFAULT_AI_RESERVE_PER_DIRECTION
    assert explicit.ai.reserve_per_direction == 4


def test_reserve_per_direction_can_be_disabled(tmp_path) -> None:
    config = load_config(_write(tmp_path, "ai:\n  reserve_per_direction: 0\n"))

    assert config.ai.reserve_per_direction == 0


def test_a_negative_reserve_is_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError, match="reserve_per_direction must be non-negative"):
        load_config(_write(tmp_path, "ai:\n  reserve_per_direction: -1\n"))


def test_the_decision_log_path_is_configurable(tmp_path) -> None:
    config = load_config(
        _write(tmp_path, "ai:\n  enabled: true\n  decision_log: /tmp/my-decisions.jsonl\n")
    )

    assert config.ai.decision_log == "/tmp/my-decisions.jsonl"


def test_an_unsupported_provider_is_rejected_by_name(tmp_path) -> None:
    path = _write(tmp_path, "ai:\n  enabled: true\n  provider: anthropic\n")

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    message = str(excinfo.value)
    assert "anthropic" in message
    assert "openai" in message


def test_negative_max_retries_is_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError, match="max_retries must be non-negative"):
        load_config(_write(tmp_path, "ai:\n  max_retries: -1\n"))


def test_out_of_range_temperature_is_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError, match="temperature must be between 0 and 2"):
        load_config(_write(tmp_path, "ai:\n  temperature: 3.5\n"))


def test_negative_token_costs_are_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError, match="token costs must be non-negative"):
        load_config(_write(tmp_path, "ai:\n  input_cost_per_mtok: -1\n"))


def test_the_example_config_still_loads() -> None:
    # The shipped example documents the new block; it must remain valid.
    config = load_config("config.example.yaml")

    assert config.ai.enabled is False
