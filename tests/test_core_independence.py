"""Ensures the analysis core is importable without the CLI stack (typer/rich).

The core must be reusable by a future execution layer or alternative interface, so
importing it must not drag in the presentation dependencies.
"""

from __future__ import annotations

import subprocess
import sys


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
        "import trader.ranking\n"
        "import trader.runner\n"
        "import trader.market_data\n"
        "import trader.indicators\n"
        "import trader.structure\n"
        "import trader.direction\n"
        "import trader.scoring_model\n"
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
