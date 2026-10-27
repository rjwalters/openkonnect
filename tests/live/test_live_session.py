"""Live tests against a real KiCad 10. Never run in CI.

Setup: start KiCad 10 with 'Enable KiCad API' on, and open
``tests/fixtures/board.kicad_pcb`` in the PCB editor. Then run
``uv run pytest -m live_kicad -rs``. Without a reachable KiCad these skip.
"""

from __future__ import annotations

import os

import pytest

from openkonnect.tools.board_summary import board_summary
from openkonnect.tools.session_status import session_status

from ..conftest import FIXTURE_BOARD

pytestmark = pytest.mark.live_kicad


def _require_fixture_open(session) -> dict:
    status = session.run_tool(session_status)
    boards = [d for d in status["documents"] if d["kind"] == "board"]
    names = [os.path.basename(d["path"]) for d in boards]
    if os.path.basename(FIXTURE_BOARD) not in names:
        pytest.skip(f"live_kicad: open {FIXTURE_BOARD} in KiCad (open boards: {names})")
    return status


def test_session_status_reports_open_board(live_session):
    status = _require_fixture_open(live_session)
    assert status["ok"] is True
    assert status["kicad_running"] is True
    assert status["ipc_reachable"] is True
    assert status["kicad_version"].startswith("10.")
    board = next(d for d in status["documents"] if d["kind"] == "board")
    assert board["open"] is True
    assert board["dirty"] == "unknown"
    assert board["dirty_basis"] == "none"


def test_board_summary_matches_fixture(live_session):
    _require_fixture_open(live_session)
    r = live_session.run_tool(board_summary)
    assert r["ok"] is True, r
    assert r["title_block"]["title"] == "openkonnect live fixture"
    assert r["layers"]["copper"] == 2
    assert r["counts"]["footprints"] == 1
    assert r["counts"]["nets"] == 2
    assert r["counts"]["tracks"] == 1
    assert r["counts"]["vias"] == 1
    assert r["counts"]["zones"] == 0
    assert r["outline"]["width_mm"] == pytest.approx(30.0, abs=0.2)
    assert r["outline"]["height_mm"] == pytest.approx(20.0, abs=0.2)
