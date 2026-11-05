"""LAY-02 ``list_footprints`` against a fake PCB editor."""

from __future__ import annotations

import copy

from openkonnect.tools.list_footprints import list_footprints

from .conftest import EditorFake, make_session, selection_board_index


def run(backend, **kw):
    return make_session(backend).run_tool(lambda s: list_footprints(s, **kw))


def refs(r):
    return [f["ref"] for f in r["footprints"]]


def test_lists_every_footprint_of_the_selection_fixture():
    r = run(EditorFake())
    assert r["ok"] is True, r
    assert r["count"] == 3
    assert refs(r) == ["R1", "R2", "R3"]
    r1 = r["footprints"][0]
    assert r1["value"] == "10k"
    assert r1["library_id"] == "openkonnect:R_Fixture"
    assert r1["position_mm"] == {"frame": "file", "x_mm": 110.0, "y_mm": 60.0}
    # Edge.Cuts starts at (100, 50), so the board frame is the file frame minus that.
    assert r1["position_board_mm"] == {"frame": "board", "x_mm": 10.0, "y_mm": 10.0}
    assert r1["rotation_deg"] == 0.0
    assert r1["side"] == "front"
    assert r1["locked"] is False
    assert r1["fields"] == {}
    assert r1["attributes"]["do_not_populate"] is False
    assert r1["pad_count"] == 2
    assert r["frame_offset"]["x_mm"] == 100.0 and r["frame_offset"]["y_mm"] == 50.0
    assert r["source"] == "live_editor"


def test_no_open_board_is_no_document():
    r = make_session(EditorFake(open_boards=[[]])).run_tool(list_footprints)
    assert r["ok"] is False
    assert r["error"]["code"] == "no_document"


def test_refs_filter_and_empty_match():
    r = run(EditorFake(), refs=["R3", "R1"])
    assert refs(r) == ["R1", "R3"]
    assert r["refs_not_on_board"] == []
    r = run(EditorFake(), refs=["R2", "U9"])
    assert refs(r) == ["R2"]
    assert r["refs_not_on_board"] == ["U9"]
    r = run(EditorFake(), refs=["U9"])
    assert r["ok"] is True and r["footprints"] == [] and r["count"] == 0


def _two_sided():
    index = selection_board_index()
    fp = index["footprints"][1]  # R2 goes to the back
    fp["layer"] = "B.Cu"
    fp["orientation_deg"] = 270.0
    fp["locked"] = True
    return EditorFake(index)


def test_side_filter_and_empty_match():
    fake = _two_sided()
    assert refs(run(fake, side="back")) == ["R2"]
    assert refs(run(fake, side="front")) == ["R1", "R3"]
    r = run(fake, side="back")
    assert r["footprints"][0]["rotation_deg"] == 270.0
    assert r["footprints"][0]["locked"] is True
    assert run(EditorFake(), side="back")["footprints"] == []


def test_invalid_side_is_invalid_argument():
    r = run(EditorFake(), side="top")
    assert r["ok"] is False and r["error"]["code"] == "invalid_argument"


def test_box_filter_in_file_frame_and_empty_match():
    box = {"frame": "file", "x0_mm": 105, "y0_mm": 55, "x1_mm": 121, "y1_mm": 61}
    assert refs(run(EditorFake(), box=box)) == ["R1", "R2"]
    far = {"frame": "file", "x0_mm": 0, "y0_mm": 0, "x1_mm": 10, "y1_mm": 10}
    r = run(EditorFake(), box=far)
    assert r["ok"] is True and r["footprints"] == []


def test_box_filter_in_board_frame_and_reversed_corners():
    # R3 is at board (30, 15). Corners given in reverse order still work.
    box = {"frame": "board", "x0_mm": 35, "y0_mm": 20, "x1_mm": 25, "y1_mm": 10}
    assert refs(run(EditorFake(), box=box)) == ["R3"]


def test_box_needs_an_explicit_frame_and_numbers():
    r = run(EditorFake(), box={"x0_mm": 0, "y0_mm": 0, "x1_mm": 1, "y1_mm": 1})
    assert r["error"]["code"] == "invalid_argument"
    r = run(EditorFake(), box={"frame": "file", "x0_mm": "a", "y0_mm": 0, "x1_mm": 1, "y1_mm": 1})
    assert r["error"]["code"] == "invalid_argument"


def test_board_frame_box_without_an_outline_is_refused():
    index = selection_board_index()
    index["outline_nm"] = None
    box = {"frame": "board", "x0_mm": 0, "y0_mm": 0, "x1_mm": 1, "y1_mm": 1}
    r = run(EditorFake(index), box=box)
    assert r["error"]["code"] == "invalid_argument"
    # The file frame still works, with no board-frame position.
    r = run(EditorFake(index), box={**box, "frame": "file", "x1_mm": 200, "y1_mm": 200})
    assert r["count"] == 3
    assert "position_board_mm" not in r["footprints"][0]


def test_selected_only_and_empty_selection():
    fake = EditorFake()
    assert run(fake, selected_only=True)["footprints"] == []
    fake.selected = [fake.id_of("R2"), fake.id_of("R3.1")]  # a pad is not a footprint
    r = run(fake, selected_only=True)
    assert refs(r) == ["R2"]


def test_filters_combine():
    fake = _two_sided()
    fake.selected = [fake.id_of("R1"), fake.id_of("R2")]
    assert refs(run(fake, selected_only=True, side="front")) == ["R1"]
    assert run(fake, selected_only=True, refs=["R3"])["footprints"] == []


def test_missing_details_are_null_not_guessed():
    index = selection_board_index()
    index["footprints"][0] = {
        k: v
        for k, v in index["footprints"][0].items()
        if k not in ("value", "library_id", "orientation_deg", "locked", "attributes", "fields")
    }
    r = run(EditorFake(index))
    f = r["footprints"][0]
    assert f["value"] is None and f["library_id"] is None
    assert f["rotation_deg"] is None and f["locked"] is None


def test_footprint_without_value_is_listed():
    index = copy.deepcopy(selection_board_index())
    index["footprints"][0]["value"] = ""
    r = run(EditorFake(index))
    assert r["footprints"][0]["value"] == ""
