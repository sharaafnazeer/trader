"""Suite-wide fixtures.

The only thing here is colour neutralisation, and it exists because three CLI tests were
failing for everybody whose terminal advertises colour. ``rich`` — which Typer renders
``--help`` through — consults ``FORCE_COLOR``/``NO_COLOR`` in the real environment rather
than anything the test passes in, so on a colour-capable terminal it wraps option names in
ANSI escapes and a plain ``"--ai" in result.output`` no longer matches. The assertions were
right; the output was merely dressed up.

Pinning the environment here rather than patching each assertion keeps the tests reading as
statements about the CLI instead of statements about escape codes, and it fixes the whole
class at once: any future test that inspects rendered output is uncoloured, whether it runs
on a developer's terminal or in CI.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

# Colour only. Width is deliberately left alone: setting ``COLUMNS`` here would leak into
# the subprocess in test_cli_table_widths.py, where the whole point is what the console
# does when nobody has set a width.
_FIXED_ENVIRONMENT = {"NO_COLOR": "1", "TERM": "dumb", "FORCE_COLOR": None}


@pytest.fixture(autouse=True, scope="session")
def _deterministic_console_output() -> Iterator[None]:
    """Render CLI output identically regardless of the terminal the suite is run from."""

    previous = {key: os.environ.get(key) for key in _FIXED_ENVIRONMENT}
    for key, value in _FIXED_ENVIRONMENT.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
