"""Pushing high-conviction verdicts to the trader's phone.

This is the slice that removes the desk. Everything upstream produces opinions on a
terminal nobody is watching; this delivers the ones worth acting on to Telegram.

Two rules shape the design:

* **Confidence alone does not authorise an alert.** A high-confidence WAIT is still a
  WAIT, and pushing it to a phone inverts its meaning. Only ``LONG`` and ``SHORT``
  verdicts are ever sent — see :func:`should_alert`.
* **Delivery is not allowed to cost a scan.** :class:`TelegramNotifier` raises
  :class:`NotifyError` and the application layer reports and swallows it; a messaging
  outage must never discard analysis the trader already paid to compute.

Formatting is a pure function, separate from sending, so the exact text that would reach
a phone is assertable without a network. The transport is one stdlib POST — no HTTP
dependency is added — and the sender itself is injectable, so no test ever opens a socket.

This module belongs to the analysis core: it does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Protocol

from trader.analyst import Action, AnalystVerdict
from trader.brief import SetupBrief
from trader.decision_log import SCANNER_SCAN

logger = logging.getLogger(__name__)

# Telegram's Bot API endpoint. The bot token is part of the URL, which is exactly why
# every error message from this module is passed through :func:`_redact` first.
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

# Seconds to wait on the send before giving up. Short: an alert that arrives a minute late
# is worthless, and the run should not stall behind it.
SEND_TIMEOUT_SECONDS = 10.0

# Appended to every message. The trader may be reading this half-awake on a phone; there
# must be no ambiguity about whether money has already moved.
ADVISORY_FOOTER = "Advisory only — no order has been placed and none can be."

# ``(url, form_fields) -> None``. Injected so delivery is testable without a network.
PostFn = Callable[[str, dict[str, str]], None]

_ACTION_MARK: dict[Action, str] = {
    Action.LONG: "🟢 LONG",
    Action.SHORT: "🔴 SHORT",
    Action.WAIT: "🟡 WAIT",
    Action.AVOID: "⚪ AVOID",
}


class NotifyError(Exception):
    """Raised when an alert could not be delivered."""


class Notifier(Protocol):
    """Delivers a plain-text alert somewhere the trader will see it."""

    def send(self, text: str) -> None: ...


class NullNotifier:
    """The notifier used when alerting is off: it delivers nothing, silently."""

    def send(self, text: str) -> None:
        return None


def should_alert(verdict: AnalystVerdict, min_confidence: float) -> bool:
    """Whether this verdict is worth interrupting the trader for.

    Actionable *and* confident. The action test is not redundant with the confidence
    test: the model is asked to express real uncertainty, so it can legitimately return a
    WAIT at high confidence — it is confident that the right move is to do nothing. That
    must not buzz a phone.
    """

    return verdict.is_actionable and verdict.confidence >= min_confidence


def _levels_block(verdict: AnalystVerdict) -> list[str]:
    """The trade levels, formatted for a narrow phone screen."""

    lines: list[str] = []
    if verdict.entry is not None:
        lines.append(f"Entry  {verdict.entry:g}")
    if verdict.stop_loss is not None:
        lines.append(f"Stop   {verdict.stop_loss:g}")
    if verdict.take_profits:
        lines.append("Target " + " / ".join(f"{target:g}" for target in verdict.take_profits))
    if verdict.invalidation:
        lines.append(f"Invalid if: {verdict.invalidation}")
    return lines


def format_alert(
    verdict: AnalystVerdict,
    brief: SetupBrief,
    *,
    concentration_warning: str | None = None,
    scanner: str = SCANNER_SCAN,
) -> str:
    """Render the message a trader receives — everything needed to decide, nothing else.

    The engine's own verdict is included alongside the analyst's so a disagreement is
    visible on the phone rather than only in the terminal, and the scanner is named so a
    trend setup is never mistaken for a momentum one.
    """

    mark = _ACTION_MARK[verdict.action]
    lines = [f"{mark} {verdict.symbol}  ·  confidence {verdict.confidence:.0f}/100", ""]
    lines.extend(_levels_block(verdict))
    lines.append("")
    lines.append(verdict.rationale)

    if verdict.key_risks:
        lines.append("")
        lines.append("Risks: " + "; ".join(verdict.key_risks))

    if concentration_warning:
        lines.append("")
        lines.append(f"⚠ Concentration: {concentration_warning}")

    lines.append("")
    # The momentum scanner still scores 0-100; the trend engine's score was retired, so its
    # line carries the direction alone rather than "None/100".
    engine = f"Engine ({scanner}): {brief.direction.value}"
    if brief.total is not None:
        engine += f" {brief.total:.1f}/100"
    if not verdict.agrees_with_engine:
        engine += "  — ANALYST DISAGREES"
    lines.append(engine)
    if brief.limited_history:
        lines.append("Limited price history — lower confidence.")

    lines.append("")
    lines.append(ADVISORY_FOOTER)
    return "\n".join(lines)


def _redact(text: str, secret: str) -> str:
    """Replace a secret with a placeholder wherever it appears.

    The bot token travels in the request URL, and both ``urllib`` errors and tracebacks
    happily quote that URL back. Every message leaving this module goes through here, so a
    token cannot reach a terminal or a log file by way of an error string.
    """

    return text.replace(secret, "***") if secret else text


def urllib_post(url: str, fields: dict[str, str]) -> None:
    """POST form fields with the standard library. The default Telegram transport.

    Kept as a module-level function rather than a method so it can be swapped wholesale in
    tests, and so no HTTP dependency is added to the project for one request.
    """

    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_SECONDS) as response:
        status = getattr(response, "status", 200)
        if status >= 400:
            raise NotifyError(f"Telegram returned HTTP {status}")


class TelegramNotifier:
    """Delivers alerts to one Telegram chat through the Bot API."""

    def __init__(self, bot_token: str, chat_id: str, post: PostFn = urllib_post) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._post = post

    def send(self, text: str) -> None:
        """Send one message, converting any transport failure into :class:`NotifyError`.

        ``disable_web_page_preview`` keeps a wall of link previews out of the chat, and no
        ``parse_mode`` is set so a rationale containing ``*`` or ``_`` is delivered
        literally rather than rejected as malformed markup.
        """

        url = TELEGRAM_API_URL.format(token=self._bot_token)
        fields = {
            "chat_id": self._chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
        try:
            self._post(url, fields)
        except NotifyError as exc:
            raise NotifyError(_redact(str(exc), self._bot_token)) from None
        except urllib.error.HTTPError as exc:
            raise NotifyError(
                _redact(f"Telegram returned HTTP {exc.code} {exc.reason}", self._bot_token)
            ) from None
        except urllib.error.URLError as exc:
            raise NotifyError(
                _redact(f"could not reach Telegram: {exc.reason}", self._bot_token)
            ) from None
        except Exception as exc:  # noqa: BLE001 - any transport failure is a NotifyError
            raise NotifyError(
                _redact(f"could not send the Telegram alert: {exc}", self._bot_token)
            ) from None
