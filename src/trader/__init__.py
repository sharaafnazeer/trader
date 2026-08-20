"""Advisory crypto trading bot core package.

This package holds the analysis core (data provider, strategy checklist, orchestration).
It deliberately does not import the CLI, ``typer`` or ``rich`` so the core stays
usable from other interfaces or a future execution layer.
"""
