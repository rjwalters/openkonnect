"""The ``--live`` opt-in for ``live_kicad`` tests (#40).

Tests marked ``live_kicad`` drive the open KiCad editor (they save, reload and
change its selection), so they never run by accident: without ``--live`` every
one is skipped at collection, before any fixture runs, whatever ``-m``
expression or node id is given. ``uv run pytest -rs`` runs every other test and
lists the live ones as skipped. The live command is
``uv run pytest --live -m live_kicad -rs``; with ``--live`` the tests still
skip on their own when KiCad is not reachable or the wrong board is open.

``tests/conftest.py`` imports the two hooks, so pytest registers them from the
conftest; ``tests/test_live_opt_in.py`` imports them into a sandbox conftest.
"""

from __future__ import annotations

import pytest

LIVE_SKIP_REASON = "live_kicad: pass --live to run (drives the open KiCad editor)"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="run the live_kicad tests, which drive the open KiCad editor",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip every ``live_kicad`` test unless ``--live`` is given (#40)."""
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason=LIVE_SKIP_REASON)
    for item in items:
        if item.get_closest_marker("live_kicad") is not None:
            item.add_marker(skip)
