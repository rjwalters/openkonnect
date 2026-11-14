"""LAY-08 live: ``board_outline`` against the selection fixture open in KiCad 10.

Never runs in CI. Setup as in ``test_live_selection.py``: start KiCad 10 with
'Enable KiCad API' on, open ``tests/fixtures/selection.kicad_pcb`` (unmodified)
in the PCB editor, then run ``uv run pytest --live -m live_kicad -rs``.
It only reads; it never edits, selects or saves.

**Fixture board:** ``tests/fixtures/selection.kicad_pcb``. Its only Edge.Cuts
geometry is one board-level ``gr_rect`` from 100,50 to 140,75 mm with a 0.1 mm
stroke, so this covers the single self-closing rectangle path and the C-1
frames. The chaining, cutout, curve-sampling and open-outline paths have no
fixture board and are covered by the unit tests in ``tests/test_edge_outline.py``
against real kipy shapes, not live.
"""

from __future__ import annotations

import os

import pytest

from openkonnect.tools.board_outline import board_outline

from ..conftest import SELECTION_BOARD

pytestmark = pytest.mark.live_kicad


@pytest.fixture
def opened(live_session):
    r = live_session.run_tool(lambda s: {"boards": s.call(lambda b: b.open_boards())})
    names = [os.path.basename(b.path) for b in r.get("boards") or [] if b.path]
    if os.path.basename(SELECTION_BOARD) not in names:
        pytest.skip(f"live_kicad: open {SELECTION_BOARD} in KiCad (open boards: {names})")
    return live_session


def test_the_fixture_outline_is_one_closed_rectangle(opened):
    r = opened.run_tool(board_outline)
    assert r["ok"], r
    outline = r["outline"]
    assert outline["present"] is True
    assert outline["closed"] is True
    assert outline["reason"] is None
    assert outline["loop_count"] == 1
    assert outline["open_chains"] == []
    [loop] = outline["loops"]
    assert loop["closed"] is True
    assert loop["role"] == "outline"
    assert loop["frame"] == "file"
    assert loop["point_count"] == 4
    assert loop["source_shapes"] == 1
    assert {(p["x_mm"], p["y_mm"]) for p in loop["points_mm"]} == {
        (100.0, 50.0),
        (140.0, 50.0),
        (140.0, 75.0),
        (100.0, 75.0),
    }


def test_the_live_extents_exclude_the_stroke_width(opened):
    r = opened.run_tool(board_outline)
    assert r["ok"], r
    extents = r["extents"]
    # 0.1 mm stroke: KiCad's own bounding box would be 40.1 x 25.1 mm.
    assert (extents["width_mm"], extents["height_mm"]) == (40.0, 25.0)
    assert (extents["x0_mm"], extents["y0_mm"]) == (100.0, 50.0)
    assert extents["frame"] == "file"


def test_the_live_frame_offset_and_origins_are_reported(opened):
    r = opened.run_tool(board_outline)
    assert r["ok"], r
    assert r["frame_offset"]["present"] is True
    assert (r["frame_offset"]["x_mm"], r["frame_offset"]["y_mm"]) == (100.0, 50.0)
    for which in ("grid", "drill"):
        origin = r["origins"][which]
        assert origin["present"] is True, origin
        assert origin["file"]["frame"] == "file"
        assert origin["board"]["frame"] == "board"
        # board = file - offset, for whatever origin the editor currently holds.
        assert origin["board"]["x_mm"] == pytest.approx(origin["file"]["x_mm"] - 100.0)
        assert origin["board"]["y_mm"] == pytest.approx(origin["file"]["y_mm"] - 50.0)
