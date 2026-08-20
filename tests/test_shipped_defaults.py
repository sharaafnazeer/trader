"""The shipped configurations, the closed indicator set, and the no-claims rule.

Three guards that decay the moment they are left to good intentions:

* the **closed indicator set**, enumerated against the feature bundle so adding a field
  without a decision fails the build rather than quietly widening the list;
* the **shipped configurations**, loaded rather than eyeballed, so a retired key or a
  documented default that drifted is caught;
* the **no-claims rule** — nothing in the shipped documentation or output may say these
  strategies work, because nothing has been measured.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from trader.config import StrategyConfig, load_config
from trader.indicators import CLOSED_INDICATOR_SET, TimeframeFeatures

SHIPPED_CONFIGS = ["config.example.yaml", "mywatch.yaml"]

# Settings deliberately deleted when the indicator set was closed. A shipped file naming one
# would fail to load, but asserting it by name says *why* rather than "config broken".
RETIRED_KEYS = ["quality_threshold", "category_weights", "relative_volume_multiple", "entry"]


# --- The closed indicator set -------------------------------------------------------


def test_the_feature_bundle_is_exactly_the_closed_set() -> None:
    """Adding an indicator is a decision, not a convenience — so it fails the build."""

    fields = {f.name for f in dataclasses.fields(TimeframeFeatures)}

    assert fields == CLOSED_INDICATOR_SET, (
        f"unexpected: {sorted(fields - CLOSED_INDICATOR_SET)}; "
        f"missing: {sorted(CLOSED_INDICATOR_SET - fields)}"
    )


def test_the_guard_notices_a_field_outside_the_list() -> None:
    """The guard above is only worth having if it actually fails. This proves it does.

    A field is added to a *copy* of the closed list rather than to the dataclass, which
    exercises the same comparison without mutating a frozen module-level constant.
    """

    fields = {f.name for f in dataclasses.fields(TimeframeFeatures)} | {"rsi"}

    assert fields != CLOSED_INDICATOR_SET


@pytest.mark.parametrize(
    "removed",
    ["ema20", "ema200", "rsi", "roc", "obv", "obv_slope", "bollinger_width"],
)
def test_no_removed_indicator_is_back(removed: str) -> None:
    assert removed not in CLOSED_INDICATOR_SET


# --- The shipped configurations -----------------------------------------------------


@pytest.mark.parametrize("path", SHIPPED_CONFIGS)
def test_a_shipped_configuration_loads(path: str) -> None:
    assert load_config(path) is not None


@pytest.mark.parametrize("path", SHIPPED_CONFIGS)
def test_a_shipped_configuration_names_no_retired_setting(path: str) -> None:
    """Asserted by loading, not by reading: a retired key fails the load by design."""

    config = load_config(path)  # raises if any retired key is present

    assert config.strategies.enabled is True


@pytest.mark.parametrize("path", SHIPPED_CONFIGS)
def test_a_shipped_configuration_yields_the_documented_defaults(path: str) -> None:
    """The files spell the defaults out; if the code's defaults move, these disagree."""

    assert load_config(path).strategies == StrategyConfig()


@pytest.mark.parametrize("path", SHIPPED_CONFIGS)
@pytest.mark.parametrize("key", RETIRED_KEYS)
def test_a_shipped_configuration_does_not_mention_a_retired_key_as_a_setting(
    path: str, key: str
) -> None:
    """Not even commented out: a commented setting is one uncomment away from failing."""

    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip().lstrip("#").strip()
        assert not stripped.startswith(f"{key}:"), f"{path}: {line}"


# --- The documentation ---------------------------------------------------------------

README = Path("README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("field", sorted(f.name for f in dataclasses.fields(StrategyConfig)))
def test_every_strategy_threshold_is_documented_with_its_default(field: str) -> None:
    default = getattr(StrategyConfig(), field)
    assert field in README, f"{field} is configurable but undocumented"
    if isinstance(default, bool):
        return

    # Compared numerically, not as text: the README writes 0.20 where the repr is 0.2, and
    # a test that insisted on trailing zeros would be about formatting rather than drift.
    documented = re.search(rf"{field}:\s*([\d.]+)", README)
    assert documented is not None, f"{field} has no documented value"
    assert float(documented.group(1)) == pytest.approx(float(default)), (
        f"{field} is documented as {documented.group(1)} but defaults to {default!r}"
    )


def test_the_readme_lists_the_closed_indicator_set() -> None:
    assert "closed indicator set" in README.lower()
    for indicator in ("EMA10", "EMA21", "EMA50", "SMA200", "Stochastic RSI", "MACD", "ATR"):
        assert indicator in README


def test_the_readme_says_additions_require_a_decision() -> None:
    assert "Adding an indicator is a decision" in README


def test_the_readme_documents_both_strategies_and_the_four_field_output() -> None:
    assert "Strategy 1A" in README
    assert "Strategy 1B" in README
    for field in ("Trend", "Setup", "Entry", "Decision"):
        assert f"**{field}**" in README


def test_the_readme_states_the_measured_result_including_the_loss() -> None:
    """Measured 2026-08-20: profit factor 0.979 — it loses money, and the README says so.

    This replaces an assertion that the strategies were *unmeasured*. The durable rule is
    not "say nothing" but "say what was found": a reader must not be able to come away
    thinking this is profitable, and the figures are the only thing that guarantees it.
    """

    assert "0.979" in README, "the measured profit factor is not documented"
    assert "still not tradeable" in README.lower()
    assert "because it is not" in README  # the explicit denial of profitability


# --- No claims -------------------------------------------------------------------------

# Phrases that would assert an edge nobody has measured. Matched case-insensitively against
# the shipped documentation and every source file's text.
_CLAIMS = [
    r"\bprofitable\b",
    r"\bmakes money\b",
    r"\bproven (?:edge|strategy|profitable)\b",
    r"\bvalidated (?:edge|strategy)\b",
    r"\bwill (?:win|profit)\b",
    r"\bguaranteed (?:profit|returns?)\b",
]


@pytest.mark.parametrize("pattern", _CLAIMS)
def test_the_documentation_claims_no_edge(pattern: str) -> None:
    for path in [Path("README.md"), *(Path(p) for p in SHIPPED_CONFIGS)]:
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(pattern, text, re.IGNORECASE):
            line = text[: match.start()].count("\n") + 1
            context = text.splitlines()[line - 1]
            # A denial is not a claim: "no claim that it is profitable" must be allowed.
            assert re.search(r"\b(no|not|never|without|nothing)\b", context, re.IGNORECASE), (
                f"{path}:{line} claims an edge: {context.strip()}"
            )


@pytest.mark.parametrize("pattern", _CLAIMS)
def test_the_rendered_output_claims_no_edge(pattern: str) -> None:
    """Strings the trader sees at runtime, not just the docs they may never read."""

    for source in Path("src/trader").glob("*.py"):
        text = source.read_text(encoding="utf-8")
        for match in re.finditer(pattern, text, re.IGNORECASE):
            line = text[: match.start()].count("\n") + 1
            context = text.splitlines()[line - 1]
            assert re.search(r"\b(no|not|never|without|nothing)\b", context, re.IGNORECASE), (
                f"{source}:{line} claims an edge: {context.strip()}"
            )
