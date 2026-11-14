"""LAY-05 ``list_copper``: filters and shaping against a fake PCB editor, and the decode.

The decode tests at the bottom build real ``kipy`` ``Track`` / ``ArcTrack`` / ``Via``
objects on a minimal board fake and call ``backend._copper`` directly, so the field
names, units and geometry the real KiCad path depends on are exercised with no KiCad
running (the pattern of ``tests/test_edge_outline.py``). They are not a substitute for
a live run: see ``tests/live/test_live_list_copper.py``.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

from kipy.board_types import ArcTrack, BoardRectangle, Track, Via
from kipy.geometry import Vector2
from kipy.proto.board.board_types_pb2 import BoardLayer, ViaType

from openkonnect.backend import _copper
from openkonnect.tools.list_copper import list_copper

from .conftest import MM, EditorFake, FakeBackend, board_ref, make_session


def run(backend, **kw):
    return make_session(backend).run_tool(lambda s: list_copper(s, **kw))


def ids(r):
    return [c["id"] for c in r["copper"]]


def by_id(r):
    return {c["id"]: c for c in r["copper"]}


# -- the whole board -------------------------------------------------------------


def test_every_track_arc_and_via_with_no_filters():
    r = run(EditorFake())
    assert r["ok"] is True, r
    assert r["count"] == 6
    # sorted by kind, then net, then id; the netless via sorts first among vias
    assert ids(r) == ["arc-1", "trk-1", "trk-2", "trk-3", "via-2", "via-1"]
    assert r["filters"] == {"net": None, "layer": None, "box": None, "locked_only": False}
    assert r["board"]["path"].endswith("selection.kicad_pcb")


def test_a_track_reports_both_frames_a_width_and_a_length():
    t = by_id(run(EditorFake()))["trk-1"]
    assert t["kind"] == "track" and t["net"] == "VIN" and t["locked"] is False
    assert t["id_scope"] == "session" and t["no_file_reference"] is True
    assert t["layer"] == "F.Cu" and t["layers"] is None
    assert t["width_mm"] == 0.125 and t["length_mm"] == 3.0
    assert t["start_mm"] == {"frame": "file", "x_mm": 109.0, "y_mm": 60.0}
    assert t["end_mm"] == {"frame": "file", "x_mm": 109.0, "y_mm": 57.0}
    # the fixture outline starts at (100, 50), so board = file - (100, 50)
    assert t["start_board_mm"] == {"frame": "board", "x_mm": 9.0, "y_mm": 10.0}
    assert t["end_board_mm"] == {"frame": "board", "x_mm": 9.0, "y_mm": 7.0}
    assert "mid_mm" not in t  # a straight track has no mid point
    assert t["diameter_mm"] is None and t["drill_mm"] is None and t["via_type"] is None


def test_an_arc_reports_its_mid_point_and_its_true_arc_length():
    a = by_id(run(EditorFake()))["arc-1"]
    assert a["kind"] == "arc" and a["layer"] == "B.Cu" and a["width_mm"] == 0.2
    assert a["mid_mm"] == {"frame": "file", "x_mm": 121.414214, "y_mm": 66.414214}
    assert a["mid_board_mm"] == {"frame": "board", "x_mm": 21.414214, "y_mm": 16.414214}
    # a quarter circle of radius 2 mm: pi mm along the arc, 2*sqrt(2) across the chord
    assert a["length_mm"] == round(math.pi, 6)
    assert a["length_mm"] != round(2 * math.sqrt(2), 6)


def test_a_via_reports_its_span_size_and_drill_and_no_length():
    v = by_id(run(EditorFake()))["via-1"]
    assert v["kind"] == "via" and v["net"] == "VIN" and v["via_type"] == "through"
    assert v["layers"] == ["F.Cu", "B.Cu"] and v["layer"] is None
    assert v["diameter_mm"] == 0.8 and v["drill_mm"] == 0.4
    assert v["position_mm"] == {"frame": "file", "x_mm": 119.0, "y_mm": 57.0}
    assert v["position_board_mm"] == {"frame": "board", "x_mm": 19.0, "y_mm": 7.0}
    assert v["width_mm"] is None  # a via's size is its diameter, not a track width
    assert v["length_mm"] is None  # a via spans layers; it has no length


def test_a_via_with_no_net_is_null_never_guessed():
    v = by_id(run(EditorFake()))["via-2"]
    assert v["net"] is None and v["via_type"] == "blind_buried"
    assert v["layers"] == ["F.Cu", "In1.Cu"]


# -- total length per net --------------------------------------------------------


def test_total_length_per_net_counts_tracks_and_arcs_only():
    totals = run(EditorFake())["total_length_mm_by_net"]
    # VIN is 3 + 10 + 3 mm of track; via-1 is on VIN and adds nothing
    assert totals == {"GND": round(math.pi, 6), "VIN": 16.0}
    # the netless via contributes no entry at all
    assert None not in totals and "" not in totals


def test_a_net_left_with_only_a_via_totals_zero():
    fake = EditorFake()
    totals = run(fake, layer="B.Cu")["total_length_mm_by_net"]
    # B.Cu keeps the GND arc and the through via on VIN, and no VIN track
    assert totals == {"GND": round(math.pi, 6), "VIN": 0.0}


def test_totals_cover_the_filtered_items_not_the_whole_board():
    r = run(EditorFake(), net="VIN", locked_only=True)
    assert ids(r) == ["trk-3"]
    assert r["total_length_mm_by_net"] == {"VIN": 3.0}  # not the full 16 mm on VIN


# -- filters ---------------------------------------------------------------------


def test_net_filter():
    r = run(EditorFake(), net="VIN")
    assert ids(r) == ["trk-1", "trk-2", "trk-3", "via-1"]
    assert r["filters"]["net"] == "VIN"


def test_layer_filter_matches_a_track_layer_and_either_end_of_a_via_span():
    assert ids(run(EditorFake(), layer="F.Cu")) == ["trk-1", "trk-2", "trk-3", "via-2", "via-1"]
    assert ids(run(EditorFake(), layer="B.Cu")) == ["arc-1", "via-1"]
    assert ids(run(EditorFake(), layer="In1.Cu")) == ["via-2"]


def test_box_filter_keeps_an_item_with_any_point_inside():
    box = {"x0_mm": 108, "y0_mm": 56, "x1_mm": 112, "y1_mm": 61, "frame": "file"}
    r = run(EditorFake(), box=box)
    # trk-1 is wholly inside; trk-2 only has its start (109, 57) inside and is kept
    assert ids(r) == ["trk-1", "trk-2"]
    assert r["filters"]["box"] == {
        "frame": "file",
        "x0_mm": 108.0,
        "y0_mm": 56.0,
        "x1_mm": 112.0,
        "y1_mm": 61.0,
    }


def test_box_filter_in_the_board_frame():
    box = {"x0_mm": 8, "y0_mm": 6, "x1_mm": 12, "y1_mm": 11, "frame": "board"}
    assert ids(run(EditorFake(), box=box)) == ["trk-1", "trk-2"]


def test_box_corners_may_be_given_in_any_order():
    box = {"x0_mm": 112, "y0_mm": 61, "x1_mm": 108, "y1_mm": 56, "frame": "file"}
    assert ids(run(EditorFake(), box=box)) == ["trk-1", "trk-2"]


def test_locked_only_filter():
    assert ids(run(EditorFake(), locked_only=True)) == ["trk-3", "via-2"]


def test_filters_combine():
    box = {"x0_mm": 100, "y0_mm": 50, "x1_mm": 140, "y1_mm": 75, "frame": "file"}
    r = run(EditorFake(), net="VIN", layer="F.Cu", box=box, locked_only=True)
    assert ids(r) == ["trk-3"]


def test_a_filter_matching_nothing_is_an_empty_list_not_an_error():
    r = run(EditorFake(), net="NOPE")
    assert r["ok"] is True and r["count"] == 0 and r["copper"] == []
    assert r["total_length_mm_by_net"] == {}
    box = {"x0_mm": 0, "y0_mm": 0, "x1_mm": 1, "y1_mm": 1, "frame": "file"}
    assert run(EditorFake(), box=box)["copper"] == []


def test_a_board_with_no_copper_at_all_is_an_empty_list():
    fake = EditorFake()
    fake.copper_list = []
    r = run(fake)
    assert r["ok"] is True and r["count"] == 0 and r["total_length_mm_by_net"] == {}


def test_a_zero_length_track_is_kept_and_adds_nothing():
    fake = EditorFake()
    fake.copper_list = [
        {
            "id": "trk-0",
            "kind": "track",
            "net": "SIG",
            "layer": "F.Cu",
            "width_nm": 125_000,
            "start_nm": {"x": 110 * MM, "y": 60 * MM},
            "end_nm": {"x": 110 * MM, "y": 60 * MM},
            "length_nm": 0.0,
            "locked": False,
        }
    ]
    r = run(fake)
    assert ids(r) == ["trk-0"]
    assert r["copper"][0]["length_mm"] == 0.0
    assert r["total_length_mm_by_net"] == {"SIG": 0.0}


# -- the board frame when there is no outline ------------------------------------


def test_without_an_outline_the_board_frame_points_are_null():
    fake = EditorFake()
    fake.index = {**fake.index, "outline_nm": None}
    r = run(fake)
    assert r["frame_offset"]["present"] is False
    t = by_id(r)["trk-1"]
    assert t["start_mm"]["x_mm"] == 109.0 and t["start_board_mm"] is None
    assert by_id(r)["via-1"]["position_board_mm"] is None


def test_box_frame_board_without_an_outline_is_invalid_argument():
    fake = EditorFake()
    fake.index = {**fake.index, "outline_nm": None}
    box = {"x0_mm": 0, "y0_mm": 0, "x1_mm": 10, "y1_mm": 10, "frame": "board"}
    r = run(fake, box=box)
    assert r["ok"] is False and r["error"]["code"] == "invalid_argument"
    assert r["error"]["details"]["frame_offset"]["present"] is False


# -- failures --------------------------------------------------------------------


def test_unknown_layer_is_invalid_argument_before_any_ipc_call():
    for bad in ("F.Copper", "In31.Cu", "User.200", "Nope.Cu"):
        fake = EditorFake()
        r = run(fake, layer=bad)
        assert r["ok"] is False and r["error"]["code"] == "invalid_argument", bad
        assert "copper" not in fake.calls, bad


def test_a_known_inner_or_user_layer_is_accepted():
    for good in ("In1.Cu", "In30.Cu", "User.199", "Edge.Cuts"):
        assert run(EditorFake(), layer=good)["ok"] is True, good


def test_non_string_filter_is_invalid_argument():
    r = run(EditorFake(), net=5)
    assert r["ok"] is False and r["error"]["code"] == "invalid_argument"


def test_non_bool_locked_only_is_invalid_argument():
    fake = EditorFake()
    r = run(fake, locked_only="yes")
    assert r["ok"] is False and r["error"]["code"] == "invalid_argument"
    assert "copper" not in fake.calls


def test_bad_box_is_invalid_argument():
    for bad in (
        {"x0_mm": 0, "y0_mm": 0, "x1_mm": 1, "y1_mm": 1},  # no frame
        {"x0_mm": 0, "y0_mm": 0, "x1_mm": 1, "y1_mm": 1, "frame": "sheet"},
        {"x0_mm": "a", "y0_mm": 0, "x1_mm": 1, "y1_mm": 1, "frame": "file"},
        "not an object",
    ):
        r = run(EditorFake(), box=bad)
        assert r["ok"] is False and r["error"]["code"] == "invalid_argument", bad


def test_no_board_open_is_no_document():
    r = run(FakeBackend(open_boards=[[]]))
    assert r["ok"] is False and r["error"]["code"] == "no_document"


# -- the KipyBackend decode path -------------------------------------------------

LAYER = BoardLayer


def _v(x_mm: float, y_mm: float) -> Vector2:
    return Vector2.from_xy(round(x_mm * MM), round(y_mm * MM))


def kipy_track(item_id: str, net: str, start, end, *, layer=LAYER.BL_F_Cu, width=125_000):
    t = Track()
    t._proto.id.value = item_id
    t._proto.net.name = net
    t.layer = layer
    t.start, t.end, t.width = _v(*start), _v(*end), width
    return t


def kipy_arc(item_id: str, net: str, start, mid, end, *, layer=LAYER.BL_B_Cu, width=200_000):
    a = ArcTrack()
    a._proto.id.value = item_id
    a._proto.net.name = net
    a.layer = layer
    a.start, a.mid, a.end, a.width = _v(*start), _v(*mid), _v(*end), width
    a.locked = True
    return a


def kipy_via(item_id: str, net: str, position, *, diameter=800_000, drill=400_000):
    v = Via()
    v._proto.id.value = item_id
    v._proto.net.name = net
    v.position = _v(*position)
    v.type = ViaType.VT_THROUGH
    v.diameter, v.drill_diameter = diameter, drill
    return v


def edge_rect(x0: float, y0: float, x1: float, y1: float) -> BoardRectangle:
    r = BoardRectangle()
    r.top_left, r.bottom_right = _v(x0, y0), _v(x1, y1)
    r.layer = LAYER.BL_Edge_Cuts
    r.attributes.stroke.width = 100_000
    return r


class BoardFake:
    """The kipy ``Board`` calls ``_copper`` makes."""

    def __init__(self, tracks: list[Any], vias: list[Any], shapes: list[Any] | None = None) -> None:
        self._tracks, self._vias, self._shapes = tracks, vias, list(shapes or [])

    def get_tracks(self) -> list[Any]:
        return list(self._tracks)

    def get_vias(self) -> list[Any]:
        return list(self._vias)

    def get_items(self, types: Any) -> list[Any]:
        return list(self._shapes)


def decode(tracks, vias, shapes=None) -> dict[str, Any]:
    board = BoardFake(tracks, vias, shapes)
    backend = SimpleNamespace(_board=lambda ref: board)
    return _copper(backend, board_ref())


def test_decode_tells_an_arc_from_a_straight_track():
    # a quarter circle of radius 2 mm around (120, 65)
    quarter = [
        (122, 65),
        (120 + 2 * math.cos(math.pi / 4), 65 + 2 * math.sin(math.pi / 4)),
        (120, 67),
    ]
    data = decode(
        [
            kipy_track("trk-1", "VIN", (109, 60), (109, 57)),
            kipy_arc("arc-1", "GND", *quarter),
        ],
        [],
    )
    items = {c["id"]: c for c in data["copper"]}
    assert items["trk-1"]["kind"] == "track" and "mid_nm" not in items["trk-1"]
    assert items["arc-1"]["kind"] == "arc" and items["arc-1"]["mid_nm"] is not None


def test_decode_reads_layers_nets_widths_and_endpoints():
    data = decode([kipy_track("trk-1", "VIN", (109, 60), (109, 57))], [])
    t = data["copper"][0]
    assert t["layer"] == "F.Cu" and t["net"] == "VIN" and t["width_nm"] == 125_000
    assert t["start_nm"] == {"x": 109 * MM, "y": 60 * MM}
    assert t["end_nm"] == {"x": 109 * MM, "y": 57 * MM}
    assert t["length_nm"] == 3.0 * MM
    assert t["locked"] is False


def test_decode_gives_an_arc_its_true_arc_length_not_its_chord():
    quarter = [
        (122, 65),
        (120 + 2 * math.cos(math.pi / 4), 65 + 2 * math.sin(math.pi / 4)),
        (120, 67),
    ]
    a = decode([kipy_arc("arc-1", "GND", *quarter)], [])["copper"][0]
    assert a["layer"] == "B.Cu" and a["locked"] is True
    assert abs(a["length_nm"] - math.pi * MM) < 1_000  # the arc length, within 1 um
    assert abs(a["length_nm"] - 2 * math.sqrt(2) * MM) > 100_000  # clearly not the chord


def test_decode_reads_a_vias_span_size_drill_and_type():
    v = decode([], [kipy_via("via-1", "VIN", (119, 57))])["copper"][0]
    assert v["kind"] == "via" and v["net"] == "VIN"
    assert v["layers"] == ["F.Cu", "B.Cu"]  # a through via's drill span
    assert v["position_nm"] == {"x": 119 * MM, "y": 57 * MM}
    assert v["diameter_nm"] == 800_000 and v["drill_nm"] == 400_000
    assert v["via_type"] == "through"
    assert "length_nm" not in v  # a via has no length to decode


def test_decode_carries_the_board_outline_for_the_board_frame():
    tracks = [kipy_track("trk-1", "VIN", (109, 60), (109, 57))]
    assert decode(tracks, [])["outline_nm"] is None
    data = decode(tracks, [], shapes=[edge_rect(100, 50, 140, 75)])
    # the geometry's own corners, not the stroked bounding box (#32)
    assert data["outline_nm"] == {
        "x0_nm": 100 * MM,
        "y0_nm": 50 * MM,
        "x1_nm": 140 * MM,
        "y1_nm": 75 * MM,
    }
