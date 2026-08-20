"""Ensures the analysis core is importable without the CLI stack (typer/rich).

The core must be reusable by a future execution layer or alternative interface, so
importing it must not drag in the presentation dependencies.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

# The AI-analyst modules. They must not be able to reach an exchange, and they must not
# read the environment: the credential is resolved once in ``trader.config`` and handed to
# them, so a key can never be picked up implicitly somewhere in the middle of a run.
AI_MODULES = [
    "analyst",
    "openai_analyst",
    "brief",
    "candidate_gate",
    "decision_log",
    "notify",
]

# Third-party HTTP clients. Telegram delivery is one POST and must stay on the standard
# library rather than pulling a dependency in for it.
HTTP_PACKAGES = {"requests", "httpx", "aiohttp", "urllib3"}


def _module_source(name: str) -> str:
    return Path("src", "trader", f"{name}.py").read_text(encoding="utf-8")


def _imported_roots(source: str) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("module", AI_MODULES)
def test_ai_modules_import_no_exchange_client(module: str) -> None:
    assert "ccxt" not in _imported_roots(_module_source(module))


@pytest.mark.parametrize("module", AI_MODULES)
def test_ai_modules_do_not_read_the_environment(module: str) -> None:
    source = _module_source(module)

    assert "os.environ" not in source
    assert "getenv" not in source
    assert "os" not in _imported_roots(source)


def test_telegram_delivery_adds_no_http_dependency() -> None:
    # One POST does not justify a dependency; ``urllib`` is stdlib.
    roots = _imported_roots(_module_source("notify"))

    assert not roots & HTTP_PACKAGES
    assert "urllib" in roots


def test_core_imports_without_typer_or_rich() -> None:
    # Run in a fresh interpreter with typer/rich blocked at import time, then import
    # every core module. If any core module imported typer or rich, this fails.
    script = (
        "import sys\n"
        "import builtins\n"
        "_real_import = builtins.__import__\n"
        "def _guard(name, *args, **kwargs):\n"
        "    top = name.split('.')[0]\n"
        "    if top in {'typer', 'rich'}:\n"
        "        raise AssertionError(f'core imported {top}')\n"
        "    return _real_import(name, *args, **kwargs)\n"
        "builtins.__import__ = _guard\n"
        "import trader\n"
        "import trader.provider\n"
        "import trader.scoring\n"
        "import trader.runner\n"
        "import trader.market_data\n"
        "import trader.indicators\n"
        "import trader.structure\n"
        "import trader.patterns\n"
        "import trader.strategy\n"
        "import trader.trendline\n"
        "import trader.retest\n"
        "import trader.direction\n"
        "import trader.momentum\n"
        "import trader.movers\n"
        "import trader.trade_planner\n"
        "import trader.replay\n"
        "import trader.trade_simulator\n"
        "import trader.metrics\n"
        "import trader.historical_data\n"
        "import trader.concurrent_loader\n"
        "import trader.backtester\n"
        "import trader.cache_refresher\n"
        "import trader.brief\n"
        "import trader.candidate_gate\n"
        "import trader.analyst\n"
        "import trader.openai_analyst\n"
        "import trader.decision_log\n"
        "import trader.notify\n"
        "print('ok')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd="src",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
