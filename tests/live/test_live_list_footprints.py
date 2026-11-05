"""LAY-02 live: ``list_footprints`` against the selection fixture open in KiCad 10.

Never runs in CI. Setup as in ``test_live_selection.py``: start KiCad 10 with
'Enable KiCad API' on, open ``tests/fixtures/selection.kicad_pcb`` (unmodified)
in the PCB editor, then run ``uv run pytest --live -m live_kicad -rs``.
It only reads; it never edits, selects or saves.
"""

from __future__ import annotations

import os

import pytest

from openkonnect.tools.list_footprints import list_footprints

from ..conftest import SELECTION_BOARD

pytestmark = pytest.mark.live_kicad


@pytest.fixture
def opened(live_session):
    r = live_session.run_tool(lambda s: {"boards": s.call(lambda b: b.open_boards())})
    names = [os.path.basename(b.path) for b in r.get("boards") or [] if b.path]
    if os.path.basename(SELECTION_BOARD) not in names:
        pytest.skip(f"live_kicad: open {SELECTION_BOARD} in KiCad (open boards: {names})")
    return live_session


def test_lists_the_selection_fixture_footprints(opened):
    r = opened.run_tool(list_footprints)
    assert r["ok"], r
    by_ref = {f["ref"]: f for f in r["footprints"]}
    assert set(by_ref) == {"R1", "R2", "R3"}
    r1 = by_ref["R1"]
    assert r1["value"] == "10k"
    assert r1["position_mm"]["x_mm"] == 110.0 and r1["position_mm"]["y_mm"] == 60.0
    assert r1["side"] == "front"
    assert r1["locked"] is False
    assert r1["rotation_deg"] == 0.0
    assert r["frame_offset"]["present"] is True


def test_filters_against_the_live_board(opened):
    r = opened.run_tool(lambda s: list_footprints(s, refs=["R2"], side="front"))
    assert [f["ref"] for f in r["footprints"]] == ["R2"]
    r = opened.run_tool(lambda s: list_footprints(s, side="back"))
    assert r["ok"] and r["footprints"] == []
