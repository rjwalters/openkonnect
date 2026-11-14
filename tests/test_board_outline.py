"""LAY-08 ``board_outline`` over the fake backend: what the result says.

The geometry itself — chaining Edge.Cuts shapes into loops, sampling curves,
excluding footprint graphics — is decoded in ``backend.py`` and tested against
real kipy shapes in ``test_edge_outline.py``. Here the backend's outline data
is given directly, so these tests pin the shape of the result: the two
"no outline" reports, the C-1 frames, and that nothing is ever filled in with
a rectangle.
"""

from __future__ import annotations

from typing import Any

from openkonnect.tools.board_outline import (
    NO_EDGE_CUTS_REASON,
    board_outline,
    describe_outline,
)
from openkonnect.tools.board_summary import board_summary

from .conftest import CANNED_BOARD, MM, EditorFake, FakeBackend, board_ref, make_session


def pt(x: float, y: float) -> dict[str, int]:
    return {"x": round(x * MM), "y": round(y * MM)}


def box(x0: float, y0: float, x1: float, y1: float) -> dict[str, int]:
    return {
        "x0_nm": round(x0 * MM),
        "y0_nm": round(y0 * MM),
        "x1_nm": round(x1 * MM),
        "y1_nm": round(y1 * MM),
    }


def ring(points: list[tuple[float, float]], role: str = "outline") -> dict[str, Any]:
    return {
        "closed": True,
        "role": role,
        "points_nm": [pt(x, y) for x, y in points],
        "source_shapes": 1,
        "gap_nm": None,
    }


def chain(points: list[tuple[float, float]], gap_mm: float, shapes: int = 3) -> dict[str, Any]:
    return {
        "closed": False,
        "role": "outline",
        "points_nm": [pt(x, y) for x, y in points],
        "source_shapes": shapes,
        "gap_nm": round(gap_mm * MM),
    }


def data(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "shape_count": 1,
        "loops": [],
        "open_chains": [],
        "outline_nm": box(100, 50, 140, 75),
        "grid_origin_nm": pt(0, 0),
        "drill_origin_nm": pt(0, 0),
        "origins_reason": None,
        "chain_tolerance_nm": 10_000,
        "arc_step_deg": 5.0,
    }
    base.update(over)
    return base


RECT = [(100.0, 50.0), (140.0, 50.0), (140.0, 75.0), (100.0, 75.0)]


def _no_rectangle_anywhere(result: dict[str, Any], extents_allowed: bool = True) -> None:
    """No four-point ring was substituted for an outline that does not exist."""
    outline = result["outline"]
    assert outline.get("loops", []) == []
    assert outline.get("loop_count", 0) == 0
    for chain_entry in outline.get("open_chains", []):
        assert chain_entry["closed"] is False
    if not extents_allowed:
        assert result["extents"] is None


# -- the ordinary case ------------------------------------------------------------


def test_a_single_rectangle_is_one_closed_four_point_loop():
    r = make_session(EditorFake()).run_tool(board_outline)
    assert r["ok"] is True, r
    outline = r["outline"]
    assert outline["present"] is True
    assert outline["closed"] is True
    assert outline["reason"] is None
    assert outline["loop_count"] == 1
    [loop] = outline["loops"]
    assert loop["closed"] is True
    assert loop["role"] == "outline"
    assert loop["frame"] == "file"
    assert loop["point_count"] == 4
    assert loop["source_shapes"] == 1
    assert [(p["x_mm"], p["y_mm"]) for p in loop["points_mm"]] == RECT
    assert "gap_mm" not in loop
    assert r["extents"]["width_mm"] == 40.0 and r["extents"]["height_mm"] == 25.0
    assert r["frame_offset"]["present"] is True
    assert (r["frame_offset"]["x_mm"], r["frame_offset"]["y_mm"]) == (100.0, 50.0)
    assert r["source"] == "live_editor"
    assert r["board"]["path"].endswith("selection.kicad_pcb")


def test_the_basis_names_the_tolerance_the_sampling_and_the_exclusions():
    r = describe_outline(data(loops=[ring(RECT)]))
    basis = r["outline"]["basis"]
    assert "0.01 mm" in basis
    assert "5.0 degrees" in basis
    assert "excluding stroke width" in basis
    assert "footprint Edge.Cuts graphics excluded" in basis


def test_a_board_with_a_cutout_keeps_two_loops_and_does_not_merge_them():
    inner = [(110.0, 55.0), (120.0, 55.0), (120.0, 65.0), (110.0, 65.0)]
    r = describe_outline(data(shape_count=2, loops=[ring(RECT), ring(inner, role="hole")]))
    outline = r["outline"]
    assert outline["loop_count"] == 2
    assert [loop["role"] for loop in outline["loops"]] == ["outline", "hole"]
    assert [loop["point_count"] for loop in outline["loops"]] == [4, 4]
    board, hole = outline["loops"]
    assert [(p["x_mm"], p["y_mm"]) for p in board["points_mm"]] == RECT
    assert [(p["x_mm"], p["y_mm"]) for p in hole["points_mm"]] == inner
    assert outline["closed"] is True


# -- the two "no outline" reports (AC 4) ------------------------------------------


def test_no_edge_cuts_shapes_is_not_present_with_board_summary_s_own_wording():
    r = describe_outline(data(shape_count=0, outline_nm=None))
    assert r["outline"] == {"present": False, "reason": NO_EDGE_CUTS_REASON}
    assert r["frame_offset"]["present"] is False
    assert r["extents"] is None
    _no_rectangle_anywhere(r, extents_allowed=False)


def test_the_no_edge_cuts_wording_is_the_one_board_summary_uses():
    """The two tools must not disagree about the same board (#88)."""
    backend = FakeBackend(
        open_boards=[[board_ref()]],
        board_data=lambda _ref: dict(CANNED_BOARD, edge_cuts_shapes=0, outline_nm=None),
    )
    summary = make_session(backend).run_tool(board_summary)
    assert summary["ok"] is True, summary
    assert summary["outline"]["reason"] == NO_EDGE_CUTS_REASON


def test_an_open_outline_is_reported_as_open_and_never_as_a_rectangle():
    three_sides = [(100.0, 50.0), (140.0, 50.0), (140.0, 75.0), (100.0, 75.0)]
    r = describe_outline(data(shape_count=3, open_chains=[chain(three_sides, gap_mm=40.0)]))
    outline = r["outline"]
    assert outline["present"] is True
    assert outline["closed"] is False
    assert "40.0 mm" in outline["reason"]
    assert "3 Edge.Cuts shape(s)" in outline["reason"]
    assert "1 open chain(s)" in outline["reason"]
    [open_chain] = outline["open_chains"]
    assert open_chain["closed"] is False
    assert open_chain["gap_mm"] == 40.0
    assert open_chain["point_count"] == 4
    assert open_chain["source_shapes"] == 3
    _no_rectangle_anywhere(r)
    # The extents are still honest, and still not a loop.
    assert r["extents"]["width_mm"] == 40.0
    assert r["frame_offset"]["present"] is True


def test_the_reason_names_the_smallest_gap_when_several_chains_are_open():
    r = describe_outline(
        data(
            shape_count=4,
            open_chains=[
                chain([(0.0, 0.0), (10.0, 0.0)], gap_mm=10.0, shapes=1),
                chain([(0.0, 20.0), (2.0, 20.0)], gap_mm=2.0, shapes=1),
            ],
        )
    )
    assert "2.0 mm" in r["outline"]["reason"]
    assert "2 open chain(s)" in r["outline"]["reason"]


def test_a_partly_closed_board_reports_both_the_loop_and_the_open_chain():
    r = describe_outline(
        data(
            shape_count=5,
            loops=[ring(RECT)],
            open_chains=[chain([(200.0, 50.0), (210.0, 50.0)], gap_mm=10.0, shapes=1)],
        )
    )
    outline = r["outline"]
    assert outline["present"] is True
    assert outline["closed"] is False, "an unclosed chain means the outline is not closed"
    assert outline["loop_count"] == 1
    assert "1 closed loop(s)" in outline["reason"]
    assert "10.0 mm" in outline["reason"]


def test_shapes_that_bound_no_area_say_so_without_inventing_a_chain():
    r = describe_outline(data(shape_count=2, loops=[], open_chains=[]))
    assert r["outline"]["closed"] is False
    assert r["outline"]["reason"] == "2 Edge.Cuts shape(s) bound no area."
    _no_rectangle_anywhere(r)


# -- origins (C-1) ----------------------------------------------------------------


def test_origins_are_given_in_both_frames_with_the_frame_named():
    r = describe_outline(data(loops=[ring(RECT)], grid_origin_nm=pt(105, 55)))
    grid = r["origins"]["grid"]
    assert grid["present"] is True
    assert grid["file"] == {"frame": "file", "x_mm": 105.0, "y_mm": 55.0}
    assert grid["board"] == {"frame": "board", "x_mm": 5.0, "y_mm": 5.0}
    drill = r["origins"]["drill"]
    assert drill["file"] == {"frame": "file", "x_mm": 0.0, "y_mm": 0.0}
    assert drill["board"] == {"frame": "board", "x_mm": -100.0, "y_mm": -50.0}


def test_the_board_frame_origin_is_null_not_zero_when_there_is_no_frame_offset():
    r = describe_outline(data(shape_count=0, outline_nm=None, grid_origin_nm=pt(3, 4)))
    grid = r["origins"]["grid"]
    assert grid["present"] is True
    assert grid["file"] == {"frame": "file", "x_mm": 3.0, "y_mm": 4.0}
    assert grid["board"] is None, "no board frame must read as unknown, never as (0, 0)"


def test_an_untouched_origin_reads_as_zero_and_the_note_says_it_is_ambiguous():
    r = make_session(EditorFake()).run_tool(board_outline)
    assert r["origins"]["grid"]["file"] == {"frame": "file", "x_mm": 0.0, "y_mm": 0.0}
    assert "never moved" in r["origins"]["note"]
    assert "(0, 0)" in r["origins"]["note"]


def test_a_kicad_without_the_origin_query_reports_absent_origins_with_a_reason():
    reason = "This KiCad does not answer GetBoardOrigin, so the origins are unknown."
    r = describe_outline(
        data(
            loops=[ring(RECT)],
            grid_origin_nm=None,
            drill_origin_nm=None,
            origins_reason=reason,
        )
    )
    for which in ("grid", "drill"):
        assert r["origins"][which] == {"present": False, "reason": reason}
    assert r["outline"]["closed"] is True, "absent origins must not fail the outline"


# -- failure modes (AC 5) ---------------------------------------------------------


def test_no_board_open_is_no_document():
    r = make_session(FakeBackend(open_boards=[[]])).run_tool(board_outline)
    assert r["ok"] is False
    assert r["error"]["code"] == "no_document"


def test_kicad_unreachable_fails_fast_before_asking_for_the_outline():
    fake = EditorFake()
    r = make_session(fake, sockets=[], running=False).run_tool(board_outline)
    assert r["ok"] is False
    assert r["error"]["code"] == "kicad_not_running"
    assert "outline" not in fake.calls


def test_the_tool_reads_the_outline_once_and_changes_nothing():
    fake = EditorFake()
    r = make_session(fake).run_tool(board_outline)
    assert r["ok"] is True, r
    assert fake.calls.count("outline") == 1
    assert fake.set_calls == []
