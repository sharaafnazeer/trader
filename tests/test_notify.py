"""Tests for alert formatting and Telegram delivery.

Delivery is exercised only through an injected sender — no test opens a socket. The
formatter is pure, so the exact text that would land on a phone is asserted directly.
"""

from __future__ import annotations

import urllib.error

import pytest

from trader.analyst import Action, AnalystVerdict
from trader.brief import LiquidityBrief, PlanBrief, SetupBrief
from trader.decision_log import SCANNER_MOVERS, SCANNER_SCAN
from trader.direction import Direction
from trader.notify import (
    ADVISORY_FOOTER,
    NotifyError,
    NullNotifier,
    TelegramNotifier,
    format_alert,
    should_alert,
)

SENTINEL_TOKEN = "123456:AA-secret-bot-token"  # noqa: S105 - a fake, for redaction tests
SENTINEL_CHAT = "-1001234567890"


def _brief(
    symbol: str = "SOLUSDT",
    direction: Direction = Direction.LONG,
    total: float = 82.5,
    *,
    limited_history: bool = False,
) -> SetupBrief:
    return SetupBrief(
        symbol=symbol,
        direction=direction,
        total=total,
        categories=(),
        timeframes=(),
        plan=PlanBrief(
            entry=363.0, stop_loss=353.3, take_profit=382.4, risk_reward=2.0, invalidation=355.0
        ),
        liquidity=LiquidityBrief(
            spread_pct=0.1, bid_depth_notional=50_000.0, ask_depth_notional=40_000.0
        ),
        limited_history=limited_history,
    )


def _verdict(
    action: Action = Action.LONG,
    confidence: float = 82.0,
    *,
    symbol: str = "SOLUSDT",
    entry: float | None = 100.0,
    stop_loss: float | None = 94.0,
    take_profits: tuple[float, ...] = (112.0, 125.0),
    rationale: str = "Trend intact with expanding volume.",
    key_risks: tuple[str, ...] = ("BTC losing its range low",),
    invalidation: str | None = "4h close below 94",
    agrees: bool = True,
) -> AnalystVerdict:
    return AnalystVerdict(
        symbol=symbol,
        action=action,
        confidence=confidence,
        entry=entry,
        stop_loss=stop_loss,
        take_profits=take_profits,
        rationale=rationale,
        key_risks=key_risks,
        invalidation=invalidation,
        agrees_with_engine=agrees,
    )


class FakePost:
    """Records every send, or raises a scripted error."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, fields: dict[str, str]) -> None:
        self.calls.append((url, fields))
        if self._error is not None:
            raise self._error


# --- Who gets alerted -----------------------------------------------------------


@pytest.mark.parametrize("action", [Action.LONG, Action.SHORT])
def test_an_actionable_verdict_at_or_above_the_floor_is_alertable(action: Action) -> None:
    assert should_alert(_verdict(action, confidence=70.0), 70.0) is True
    assert should_alert(_verdict(action, confidence=99.0), 70.0) is True


@pytest.mark.parametrize("action", [Action.LONG, Action.SHORT])
def test_a_verdict_below_the_floor_is_not_alertable(action: Action) -> None:
    assert should_alert(_verdict(action, confidence=69.9), 70.0) is False


@pytest.mark.parametrize("action", [Action.WAIT, Action.AVOID])
def test_standing_aside_is_never_alertable_however_confident(action: Action) -> None:
    # A high-confidence WAIT means "I am confident the right move is to do nothing".
    # Buzzing a phone for that inverts its meaning.
    assert should_alert(_verdict(action, confidence=100.0), 70.0) is False


# --- What the message says ------------------------------------------------------


def test_the_message_carries_everything_needed_to_act() -> None:
    text = format_alert(_verdict(), _brief())

    assert "SOLUSDT" in text
    assert "LONG" in text
    assert "confidence 82/100" in text
    assert "Entry  100" in text
    assert "Stop   94" in text
    assert "Target 112 / 125" in text
    assert "Invalid if: 4h close below 94" in text
    assert "Trend intact with expanding volume." in text
    assert "Risks: BTC losing its range low" in text


def test_every_message_states_that_nothing_was_traded() -> None:
    text = format_alert(_verdict(), _brief())

    assert ADVISORY_FOOTER in text
    assert "no order has been placed" in text


def test_the_engines_own_view_is_included() -> None:
    text = format_alert(_verdict(), _brief(direction=Direction.LONG, total=82.5))

    assert "Engine (scan): LONG 82.5/100" in text


def test_a_disagreement_is_visible_on_the_phone() -> None:
    agreeing = format_alert(_verdict(agrees=True), _brief())
    disagreeing = format_alert(
        _verdict(Action.SHORT, entry=100.0, stop_loss=106.0, take_profits=(88.0,), agrees=False),
        _brief(direction=Direction.LONG),
    )

    assert "DISAGREES" not in agreeing
    assert "ANALYST DISAGREES" in disagreeing


def test_the_concentration_warning_is_included_when_present() -> None:
    without = format_alert(_verdict(), _brief())
    with_warning = format_alert(
        _verdict(), _brief(), concentration_warning="3 of 4 are correlated L1 longs"
    )

    assert "Concentration" not in without
    assert "3 of 4 are correlated L1 longs" in with_warning


def test_the_scanner_that_produced_the_setup_is_named() -> None:
    text = format_alert(_verdict(), _brief(), scanner=SCANNER_MOVERS)

    assert f"Engine ({SCANNER_MOVERS})" in text
    assert SCANNER_SCAN not in text


def test_limited_history_is_flagged_on_the_phone() -> None:
    text = format_alert(_verdict(), _brief(limited_history=True))

    assert "Limited price history" in text


def test_a_verdict_with_no_risks_omits_the_risks_line() -> None:
    text = format_alert(_verdict(key_risks=()), _brief())

    assert "Risks:" not in text


# --- Delivery -------------------------------------------------------------------


def test_sending_posts_once_to_the_configured_chat() -> None:
    post = FakePost()

    TelegramNotifier(SENTINEL_TOKEN, SENTINEL_CHAT, post=post).send("hello")

    assert len(post.calls) == 1
    url, fields = post.calls[0]
    assert url.endswith("/sendMessage")
    assert SENTINEL_TOKEN in url  # the Bot API puts the token in the path
    assert fields["chat_id"] == SENTINEL_CHAT
    assert fields["text"] == "hello"


def test_no_parse_mode_is_set_so_punctuation_is_delivered_literally() -> None:
    # A rationale containing * or _ would be rejected as malformed markup otherwise.
    post = FakePost()

    TelegramNotifier(SENTINEL_TOKEN, SENTINEL_CHAT, post=post).send("a *b* _c_")

    _url, fields = post.calls[0]
    assert "parse_mode" not in fields
    assert fields["text"] == "a *b* _c_"


def test_the_null_notifier_sends_nothing() -> None:
    assert NullNotifier().send("anything") is None


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("connection refused"),
        RuntimeError("something else went wrong"),
        NotifyError("Telegram returned HTTP 500"),
    ],
)
def test_any_transport_failure_becomes_a_notify_error(error: Exception) -> None:
    post = FakePost(error)

    with pytest.raises(NotifyError):
        TelegramNotifier(SENTINEL_TOKEN, SENTINEL_CHAT, post=post).send("hello")


def test_an_http_error_reports_its_status() -> None:
    error = urllib.error.HTTPError(
        url=f"https://api.telegram.org/bot{SENTINEL_TOKEN}/sendMessage",
        code=403,
        msg="Forbidden",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )
    post = FakePost(error)

    with pytest.raises(NotifyError, match="403"):
        TelegramNotifier(SENTINEL_TOKEN, SENTINEL_CHAT, post=post).send("hello")


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError(
            url=f"https://api.telegram.org/bot{SENTINEL_TOKEN}/sendMessage",
            code=401,
            msg=f"Unauthorized for bot{SENTINEL_TOKEN}",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        ),
        urllib.error.URLError(f"failed for bot{SENTINEL_TOKEN}"),
        RuntimeError(f"boom while calling bot{SENTINEL_TOKEN}"),
    ],
)
def test_the_bot_token_is_redacted_from_every_error_message(error: Exception) -> None:
    # urllib errors quote the request URL back, and the token lives in that URL.
    post = FakePost(error)

    with pytest.raises(NotifyError) as excinfo:
        TelegramNotifier(SENTINEL_TOKEN, SENTINEL_CHAT, post=post).send("hello")

    assert SENTINEL_TOKEN not in str(excinfo.value)
    assert "***" in str(excinfo.value)


def test_the_original_exception_is_not_chained_so_a_traceback_cannot_leak_the_token() -> None:
    post = FakePost(urllib.error.URLError(f"failed for bot{SENTINEL_TOKEN}"))

    with pytest.raises(NotifyError) as excinfo:
        TelegramNotifier(SENTINEL_TOKEN, SENTINEL_CHAT, post=post).send("hello")

    assert excinfo.value.__cause__ is None
