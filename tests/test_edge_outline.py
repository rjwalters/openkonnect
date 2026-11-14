"""The board outline comes from Edge.Cuts geometry, not KiCad's stroked boxes (#32).

KiCad's ``GetBoundingBox`` for a shape includes its drawn line width, so a
0.1 mm outline stroke moved the board-frame origin by 50 um. These tests build
real kipy shapes on a minimal board fake whose ``get_item_bounding_box``
returns stroke-inflated boxes, so a return to the old union fails here.
"""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from kicad_tools.analysis.net_status import NetStatus, PadInfo
from kipy.board_types import (
    BoardArc,
    BoardBezier,
    BoardCircle,
    BoardPolygon,
    BoardRectangle,
    BoardSegment,
    BoardShape,
)
from kipy.geometry import Box2, PolygonWithHoles, PolyLine, PolyLineNode, Vector2
from kipy.proto.board.board_types_pb2 import BoardLayer
from kipy.proto.common.types import KiCadObjectType

from openkonnect.backend import _bezier_extent, _edge_outline, _outline
from openkonnect.errors import ErrorCode, OpenKonnectError
from openkonnect.tools.board_outline import board_outline, describe_outline
from openkonnect.tools.board_summary import board_summary
from openkonnect.tools.common import frame_offset
from openkonnect.tools.show_findings import show_findings

from .conftest import CANNED_BOARD, EditorFake, FakeBackend, board_ref, make_session

MM = 1_000_000
STROKE = 100_000  # 0.1 mm, as in tests/fixtures/selection.kicad_pcb


def v(x_mm: float, y_mm: float) -> Vector2:
    return Vector2.from_xy(round(x_mm * MM), round(y_mm * MM))


def _edge(shape: Any, layer: int = BoardLayer.BL_Edge_Cuts, stroke: int = STROKE) -> Any:
    shape.layer = layer
    shape.attributes.stroke.width = stroke
    return shape


def rect(x0: float, y0: float, x1: float, y1: float, **kw: Any) -> BoardRectangle:
    r = BoardRectangle()
    r.top_left = v(x0, y0)
    r.bottom_right = v(x1, y1)
    return _edge(r, **kw)


def seg(x0: float, y0: float, x1: float, y1: float, **kw: Any) -> BoardSegment:
    s = BoardSegment()
    s.start = v(x0, y0)
    s.end = v(x1, y1)
    return _edge(s, **kw)


def arc(start: Vector2, mid: Vector2, end: Vector2, **kw: Any) -> BoardArc:
    a = BoardArc()
    a.start, a.mid, a.end = start, mid, end
    return _edge(a, **kw)


def corner(cx: float, cy: float, r: float, start_deg: float) -> BoardArc:
    """A 90 degree arc around (cx, cy) from ``start_deg`` (y down, as in KiCad)."""

    def at(deg: float) -> Vector2:
        t = math.radians(deg)
        return v(cx + r * math.cos(t), cy + r * math.sin(t))

    return arc(at(start_deg), at(start_deg + 45), at(start_deg + 90))


def circle(cx: float, cy: float, r: float, **kw: Any) -> BoardCircle:
    c = BoardCircle()
    c.center = v(cx, cy)
    c.radius_point = v(cx + r, cy)
    return _edge(c, **kw)


def _polyline(points: list[Any]) -> PolyLine:
    """A PolyLine of point nodes; an item of three (x, y) pairs becomes an arc node."""
    line = PolyLine()
    for item in points:
        if isinstance(item[0], (int, float)):
            line.append(PolyLineNode.from_point(v(*item)))
            continue
        start, mid, end = (v(*p) for p in item)
        node = PolyLineNode()
        node.proto.arc.start.CopyFrom(start.proto)
        node.proto.arc.mid.CopyFrom(mid.proto)
        node.proto.arc.end.CopyFrom(end.proto)
        line.append(node)
    return line


def polygon(
    points: list[Any],
    holes: list[list[Any]] | None = None,
    **kw: Any,
) -> BoardPolygon:
    pwh = PolygonWithHoles()
    pwh.outline = _polyline(points)
    for hole in holes or []:
        pwh.add_hole(_polyline(hole))
    p = BoardPolygon()
    p.proto.shape.polygon.polygons.append(pwh.proto)  # as KiCad sends it
    return _edge(p, **kw)


def bezier(*pts: tuple[float, float], **kw: Any) -> BoardBezier:
    b = BoardBezier()
    b.start, b.control1, b.control2, b.end = (v(*p) for p in pts)
    return _edge(b, **kw)


def _inflated(shape: Any) -> Box2:
    """What KiCad's GetBoundingBox reports: the geometry grown by half the stroke."""
    if isinstance(shape, BoardBezier):
        pts = [shape.start, shape.control1, shape.control2, shape.end]
        box = Box2.from_points(pts)
    elif isinstance(shape, BoardSegment):
        box = Box2.from_points([shape.start, shape.end])
    else:
        box = shape.bounding_box()
    h = shape.attributes.stroke.width // 2
    return Box2.from_xywh(box.pos.x - h, box.pos.y - h, box.size.x + 2 * h, box.size.y + 2 * h)


class Board:
    """The kipy ``Board`` calls ``_edge_outline`` may use.

    ``shapes`` are board-level; ``footprints`` carry footprint-owned shapes.
    As measured on KiCad 10.0.7 (#41, docs/ipc-client.md), footprint children
    are not in board-level ``get_items([KOT_PCB_SHAPE])``: they are reachable
    only through ``get_footprints()[i].definition.shapes``, at absolute
    coordinates, with ``parent`` set to the footprint's id.
    """

    def __init__(self, *shapes: Any, footprints: list[Any] | None = None) -> None:
        self.shapes = list(shapes)
        self.footprints = list(footprints or [])
        self.requested: list[Any] = []
        self.origins_asked: list[Any] = []
        self.origin_at: dict[Any, Vector2] = {}

    def get_items(self, types: Any) -> list[Any]:
        self.requested.append(types)
        return list(self.shapes)

    def get_item_bounding_box(self, items: Any) -> list[Box2]:
        return [_inflated(s) for s in items]

    def get_shapes(self) -> list[Any]:  # kipy 0.8.0 keeps unclassified shapes too
        return list(self.shapes)

    def get_footprints(self) -> list[Any]:
        return list(self.footprints)

    def get_origin(self, origin_type: Any) -> Vector2:
        """KiCad's grid and drill origins (LAY-08); the fake keeps them at the file origin."""
        self.origins_asked.append(origin_type)
        return self.origin_at.get(origin_type, Vector2.from_xy(0, 0))


def footprint(fp_id: str, *shapes: Any) -> SimpleNamespace:
    """A footprint whose ``definition.shapes`` hold its graphics, parented to it."""
    for s in shapes:
        s._proto.parent.value = fp_id
    return SimpleNamespace(
        id=SimpleNamespace(value=fp_id), definition=SimpleNamespace(shapes=list(shapes))
    )


def box(x0: float, y0: float, x1: float, y1: float) -> dict[str, int]:
    return {
        "x0_nm": round(x0 * MM),
        "y0_nm": round(y0 * MM),
        "x1_nm": round(x1 * MM),
        "y1_nm": round(y1 * MM),
    }


def outline(*shapes: Any) -> dict[str, int] | None:
    return _edge_outline(Board(*shapes))[1]


# -- per shape type --------------------------------------------------------------


def test_stroked_rectangle_is_its_corners_not_its_drawn_box():
    board = Board(rect(100, 50, 140, 75))
    edge, got = _edge_outline(board)
    assert got == {
        "x0_nm": 100_000_000,
        "y0_nm": 50_000_000,
        "x1_nm": 140_000_000,
        "y1_nm": 75_000_000,
    }
    assert len(edge) == 1
    assert board.requested == [[KiCadObjectType.KOT_PCB_SHAPE]]


def test_rectangle_corners_need_not_be_normalized():
    assert outline(rect(140, 75, 100, 50)) == box(100, 50, 140, 75)
    assert outline(rect(140, 50, 100, 75)) == box(100, 50, 140, 75)


def test_segments_are_their_endpoints():
    shapes = [
        seg(100, 50, 140, 50),
        seg(140, 50, 140, 75),
        seg(140, 75, 100, 75),
        seg(100, 75, 100, 50),
        seg(105, 55, 120, 70),  # diagonal, inside
    ]
    assert outline(*shapes) == box(100, 50, 140, 75)


def test_arc_extent_beyond_its_endpoints():
    # A half circle of radius 10 around (120, 50) bulging upward to y = 40.
    a = arc(v(110, 50), v(120, 40), v(130, 50))
    assert outline(a) == box(110, 40, 130, 50)


def test_rounded_corner_outline_of_segments_and_arcs():
    r = 2.0
    shapes = [
        seg(100 + r, 50, 140 - r, 50),
        seg(140, 50 + r, 140, 75 - r),
        seg(140 - r, 75, 100 + r, 75),
        seg(100, 75 - r, 100, 50 + r),
        corner(100 + r, 50 + r, r, 180),
        corner(140 - r, 50 + r, r, 270),
        corner(140 - r, 75 - r, r, 0),
        corner(100 + r, 75 - r, r, 90),
    ]
    got = outline(*shapes)
    want = box(100, 50, 140, 75)
    assert all(abs(got[k] - want[k]) <= 1 for k in want), got


def test_circle_is_center_plus_radius():
    assert outline(circle(120, 60, 15)) == box(105, 45, 135, 75)


def test_polygon_is_its_outline_points():
    p = polygon([(100, 50), (140, 50), (140, 75), (120, 80), (100, 75)])
    assert outline(p) == box(100, 50, 140, 80)


def test_bezier_extent_beyond_its_endpoints_from_cubic_extrema():
    # Symmetric cubic from (100, 50) to (140, 50) with both controls at y = 30:
    # the peak at t = 0.5 is y = 50 - 0.75 * 20 = 35, short of the control hull.
    b = bezier((100, 50), (100, 30), (140, 30), (140, 50))
    assert outline(b) == box(100, 35, 140, 50)


def test_bezier_x_extremum_is_found_too():
    # Controls pull out to x = 160: peak x = 100 + 0.75 * 60 = 145 at t = 0.5.
    b = bezier((100, 50), (160, 50), (160, 75), (100, 75))
    assert outline(b) == box(100, 50, 145, 75)


def test_bezier_extent_helper_handles_a_straight_cubic():
    # Linear in t on both axes (degenerate quadratic): extent is the endpoints.
    assert _bezier_extent(v(0, 0), v(1, 1), v(2, 2), v(3, 3)) == (0, 0, 3 * MM, 3 * MM)


def test_mixed_shapes_union():
    shapes = [
        rect(100, 50, 140, 75),
        circle(150, 60, 5),
        bezier((100, 75), (100, 85), (110, 85), (110, 75)),
    ]
    assert outline(*shapes) == box(100, 50, 155, 82.5)


# -- what is not counted, and what fails ------------------------------------------


def test_no_edge_cuts_shapes_is_none():
    edge, got = _edge_outline(Board())
    assert edge == [] and got is None


def test_shapes_on_other_layers_are_ignored():
    shapes = [
        rect(100, 50, 140, 75),
        rect(0, 0, 500, 500, layer=BoardLayer.BL_F_SilkS),
        circle(0, 0, 10, layer=BoardLayer.BL_F_Cu),
    ]
    edge, got = _edge_outline(Board(*shapes))
    assert got == box(100, 50, 140, 75)
    assert len(edge) == 1


def test_only_other_layers_is_none():
    assert outline(rect(0, 0, 10, 10, layer=BoardLayer.BL_F_SilkS)) is None


def _unknown_shape(layer: int = BoardLayer.BL_Edge_Cuts) -> BoardShape:
    s = BoardShape()  # no geometry set, as kipy leaves a shape it cannot classify
    s.layer = layer
    s._proto.id.value = "e5c1-unknown"
    return s


def test_unknown_edge_cuts_geometry_raises_internal_naming_the_shape():
    with pytest.raises(OpenKonnectError) as exc:
        outline(rect(100, 50, 140, 75), _unknown_shape())
    err = exc.value
    assert err.code is ErrorCode.INTERNAL
    assert err.details["shape_id"] == "e5c1-unknown"
    assert err.details["geometry"] is None


def test_unknown_geometry_on_another_layer_is_ignored():
    assert outline(rect(100, 50, 140, 75), _unknown_shape(BoardLayer.BL_F_SilkS)) == box(
        100, 50, 140, 75
    )


# -- footprint-owned Edge.Cuts graphics (#41) ----------------------------------------

FP_EDGE_BOARD = Path(__file__).resolve().parent / "fixtures" / "fp_edge.kicad_pcb"


def _fp_edge_board(*board_shapes: Any) -> Board:
    """``tests/fixtures/fp_edge.kicad_pcb`` as KiCad 10.0.7 reports it.

    R1's Edge.Cuts line at absolute 95,45-98,45 mm lies above-left of the
    board-level 100,50-130,70 rectangle, and is not in ``get_items``.
    """
    r1 = footprint("6f1d1a3e-0001-4000-8000-000000000001", seg(95, 45, 98, 45))
    return Board(*board_shapes, footprints=[r1])


def test_footprint_edge_cuts_graphic_does_not_move_the_outline():
    edge, got = _edge_outline(_fp_edge_board(rect(100, 50, 130, 70)))
    assert got == box(100, 50, 130, 70)
    assert len(edge) == 1


def test_only_footprint_edge_cuts_graphics_is_no_outline():
    edge, got = _edge_outline(_fp_edge_board())
    assert edge == [] and got is None


def test_only_footprint_edge_cuts_graphics_gives_no_frame_offset():
    def data(_ref):
        edge, o = _edge_outline(_fp_edge_board())
        return dict(CANNED_BOARD, edge_cuts_shapes=len(edge), outline_nm=o)

    backend = FakeBackend(open_boards=[[board_ref()]], board_data=data)
    r = make_session(backend).run_tool(board_summary)
    assert r["ok"] is True, r
    assert r["outline"]["present"] is False
    assert frame_offset(_edge_outline(_fp_edge_board())[1])["present"] is False


def test_board_summary_on_the_fp_edge_board_matches_kct_origin():
    def data(_ref):
        edge, o = _edge_outline(_fp_edge_board(rect(100, 50, 130, 70)))
        return dict(CANNED_BOARD, edge_cuts_shapes=len(edge), outline_nm=o)

    backend = FakeBackend(open_boards=[[board_ref()]], board_data=data)
    r = make_session(backend).run_tool(board_summary)
    assert r["ok"] is True, r
    o = r["outline"]
    assert o["origin_mm"] == {"x": 100.0, "y": 50.0}
    assert (o["width_mm"], o["height_mm"]) == (30.0, 20.0)
    assert "footprint Edge.Cuts graphics excluded" in o["basis"]


def test_kct_board_origin_ignores_footprint_edge_cuts_graphics():
    """Pins kicad-tools' side: if upstream starts counting footprint Edge.Cuts
    graphics, the frames would disagree and this fails (kicad-tools-main CI)."""
    from kicad_tools.schema.pcb import PCB

    assert PCB.load(str(FP_EDGE_BOARD)).board_origin == (100.0, 50.0)


# -- through the tools -------------------------------------------------------------


def _fixture_board() -> Board:
    """``tests/fixtures/selection.kicad_pcb``'s outline: one 0.1 mm stroked gr_rect."""
    return Board(rect(100, 50, 140, 75))


def test_board_summary_reports_the_geometric_outline():
    def data(_ref):
        edge, o = _edge_outline(_fixture_board())
        return dict(CANNED_BOARD, edge_cuts_shapes=len(edge), outline_nm=o)

    backend = FakeBackend(open_boards=[[board_ref()]], board_data=data)
    r = make_session(backend).run_tool(board_summary)
    assert r["ok"] is True, r
    o = r["outline"]
    assert o["width_mm"] == 40.0
    assert o["height_mm"] == 25.0
    assert o["origin_mm"] == {"x": 100.0, "y": 50.0}
    assert "stroke" in o["basis"]


def test_unknown_geometry_reaches_the_caller_as_internal_error():
    def data(_ref):
        _edge_outline(Board(rect(100, 50, 140, 75), _unknown_shape()))

    backend = FakeBackend(open_boards=[[board_ref()]], board_data=data)
    r = make_session(backend).run_tool(board_summary)
    assert r["ok"] is False
    assert r["error"]["code"] == "internal_error"
    assert r["error"]["details"]["shape_id"] == "e5c1-unknown"


def test_board_frame_point_lands_on_the_file_frame_pad():
    fake = EditorFake()
    fake.index = dict(fake.index, outline_nm=_edge_outline(_fixture_board())[1])
    status = NetStatus(
        net_number=1,
        net_name="VIN",
        connected_pads=[
            PadInfo(reference="R1", pad_number="1", position=(9.0, 10.0), is_connected=True),
        ],
    )
    r = make_session(fake).run_tool(lambda s: show_findings(s, [status]))
    assert r["ok"] is True, r
    [f] = r["findings"]
    [p] = f["points"]
    assert p["frame"] == "board"
    assert p["file_mm"] == {"x_mm": 109.0, "y_mm": 60.0}
    offset = r["selection"]["frame_offset"]
    assert (offset["x_mm"], offset["y_mm"]) == (100.0, 50.0)
    assert "stroke" in offset["basis"]
    assert "footprint Edge.Cuts graphics excluded" in offset["basis"]


# -- LAY-08: Edge.Cuts shapes chained into polygons (#88) -------------------------
#
# ``_outline`` decodes the same shapes ``_edge_outline`` measures, so these use
# the same stroke-inflating board fake: a loop built from KiCad's bounding
# boxes rather than the geometry would be half a stroke too large here too.


def outline_data(board: Board) -> dict[str, Any]:
    """``_outline`` against a board fake (it only needs ``_board``)."""
    return _outline(SimpleNamespace(_board=lambda _ref: board), None)  # type: ignore[arg-type]


def loops_mm(board: Board) -> list[list[tuple[float, float]]]:
    data = outline_data(board)
    return [[(p["x"] / MM, p["y"] / MM) for p in loop["points_nm"]] for loop in data["loops"]]


def square_segments() -> list[Any]:
    return [
        seg(100, 50, 140, 50),
        seg(140, 50, 140, 75),
        seg(140, 75, 100, 75),
        seg(100, 75, 100, 50),
    ]


# -- shapes that close on their own ----------------------------------------------


def test_a_rectangle_is_one_four_corner_loop_not_its_stroked_box():
    data = outline_data(Board(rect(100, 50, 140, 75)))
    [loop] = data["loops"]
    assert loop["closed"] is True and loop["role"] == "outline"
    assert loop["source_shapes"] == 1 and loop["gap_nm"] is None
    assert [(p["x"] / MM, p["y"] / MM) for p in loop["points_nm"]] == [
        (100.0, 50.0),
        (140.0, 50.0),
        (140.0, 75.0),
        (100.0, 75.0),
    ]
    assert data["open_chains"] == []
    half = STROKE // 2 / MM
    assert all(abs(p["x"] / MM - 100.0) != half for p in loop["points_nm"])


def test_rectangle_corners_need_not_be_normalized_for_the_loop_either():
    assert loops_mm(Board(rect(140, 75, 100, 50))) == loops_mm(Board(rect(100, 50, 140, 75)))


def test_a_circle_is_a_closed_loop_of_its_own():
    data = outline_data(Board(circle(120, 60, 15)))
    [loop] = data["loops"]
    assert loop["closed"] is True and loop["source_shapes"] == 1
    assert len(loop["points_nm"]) == 72, "360 degrees at the 5 degree step"
    for p in loop["points_nm"]:
        r = math.hypot(p["x"] / MM - 120.0, p["y"] / MM - 60.0)
        assert abs(r - 15.0) < 1e-6
    assert data["outline_nm"] == box(105, 45, 135, 75)


def test_a_polygon_with_a_hole_keeps_the_hole_as_its_own_loop():
    p = polygon(
        [(100, 50), (140, 50), (140, 75), (100, 75)],
        holes=[[(110, 55), (120, 55), (120, 65), (110, 65)]],
    )
    data = outline_data(Board(p))
    assert [loop["role"] for loop in data["loops"]] == ["outline", "hole"]
    board_ring, hole = loops_mm(Board(p))
    assert board_ring == [(100.0, 50.0), (140.0, 50.0), (140.0, 75.0), (100.0, 75.0)]
    assert hole == [(110.0, 55.0), (120.0, 55.0), (120.0, 65.0), (110.0, 65.0)]
    assert all(loop["closed"] for loop in data["loops"])


def test_a_polygon_closed_explicitly_by_kicad_does_not_repeat_its_first_point():
    ring = [(100, 50), (140, 50), (140, 75), (100, 75), (100, 50)]
    [got] = loops_mm(Board(polygon(ring)))
    assert got == [(100.0, 50.0), (140.0, 50.0), (140.0, 75.0), (100.0, 75.0)]


def test_a_polygon_with_an_arc_node_is_sampled_into_the_loop():
    # A square whose top side is an arc bulging up to y = 40 (as _shape_extent
    # already handles arc nodes, the loop must sample them rather than skip them).
    p = polygon(
        [
            (100, 50),
            [(100, 50), (120, 40), (140, 50)],
            (140, 75),
            (100, 75),
        ]
    )
    [got] = loops_mm(Board(p))
    assert len(got) > 5, "the arc node contributed sampled points"
    assert min(y for _, y in got) < 45.0, "the arc bulges past its endpoints"
    assert outline_data(Board(p))["outline_nm"] == box(100, 40, 140, 75)


def test_a_board_rectangle_plus_a_cutout_rectangle_is_two_loops_not_a_union():
    data = outline_data(Board(rect(100, 50, 140, 75), rect(110, 55, 120, 65)))
    assert len(data["loops"]) == 2
    assert all(loop["closed"] for loop in data["loops"])
    assert data["open_chains"] == []
    assert data["outline_nm"] == box(100, 50, 140, 75)


# -- chaining segments, arcs and beziers -----------------------------------------


def test_four_segments_chain_into_one_closed_loop():
    data = outline_data(Board(*square_segments()))
    [loop] = data["loops"]
    assert loop["closed"] is True
    assert loop["source_shapes"] == 4
    assert loop["gap_nm"] is None
    assert len(loop["points_nm"]) == 4, "the closing point is not repeated"
    assert data["open_chains"] == []


def test_scrambled_order_and_mixed_direction_still_close():
    a, b, c, d = square_segments()
    scrambled = [
        seg(140, 75, 140, 50),  # b reversed
        seg(100, 75, 140, 75),  # c reversed
        d,
        a,
    ]
    data = outline_data(Board(*scrambled))
    [loop] = data["loops"]
    assert loop["closed"] is True and loop["source_shapes"] == 4
    assert len(loop["points_nm"]) == 4


def test_the_loop_is_the_same_whatever_order_kicad_lists_the_shapes_in():
    shapes = square_segments()
    first = loops_mm(Board(*shapes))
    for rotation in range(1, len(shapes)):
        rotated = shapes[rotation:] + shapes[:rotation]
        assert loops_mm(Board(*rotated)) == first


def test_a_chain_starting_in_the_middle_still_grows_from_both_ends():
    # The first piece tried is the one with the lowest geometry key, which sits
    # in the middle of this run: the chain must extend backwards as well.
    shapes = [
        seg(100, 50, 110, 50),
        seg(110, 50, 120, 50),
        seg(120, 50, 120, 60),
        seg(120, 60, 100, 60),
        seg(100, 60, 100, 50),
    ]
    data = outline_data(Board(*shapes))
    [loop] = data["loops"]
    assert loop["closed"] is True and loop["source_shapes"] == 5


def test_an_arc_cornered_rounded_rectangle_closes_into_one_loop():
    r = 2.0
    shapes = [
        seg(100 + r, 50, 140 - r, 50),
        seg(140, 50 + r, 140, 75 - r),
        seg(140 - r, 75, 100 + r, 75),
        seg(100, 75 - r, 100, 50 + r),
        corner(100 + r, 50 + r, r, 180),
        corner(140 - r, 50 + r, r, 270),
        corner(140 - r, 75 - r, r, 0),
        corner(100 + r, 75 - r, r, 90),
    ]
    data = outline_data(Board(*shapes))
    [loop] = data["loops"]
    assert loop["closed"] is True
    assert loop["source_shapes"] == 8
    assert len(loop["points_nm"]) > 8, "the four arcs were sampled, not straightened"
    xs = [p["x"] for p in loop["points_nm"]]
    ys = [p["y"] for p in loop["points_nm"]]
    assert abs(min(xs) - 100 * MM) <= 1 and abs(max(xs) - 140 * MM) <= 1
    assert abs(min(ys) - 50 * MM) <= 1 and abs(max(ys) - 75 * MM) <= 1


def test_a_bezier_side_chains_like_any_other_piece():
    shapes = [
        seg(100, 50, 140, 50),
        seg(140, 50, 140, 75),
        bezier((140, 75), (130, 85), (110, 85), (100, 75)),
        seg(100, 75, 100, 50),
    ]
    data = outline_data(Board(*shapes))
    [loop] = data["loops"]
    assert loop["closed"] is True and loop["source_shapes"] == 4
    assert max(p["y"] for p in loop["points_nm"]) > 75 * MM, "the curve was sampled"


def test_endpoints_within_the_tolerance_are_joined():
    gap = 9_000  # 0.009 mm, inside the 0.01 mm tolerance
    shapes = square_segments()
    shapes[0].end = Vector2.from_xy(140 * MM + gap, 50 * MM)
    data = outline_data(Board(*shapes))
    assert len(data["loops"]) == 1 and data["open_chains"] == []
    assert data["chain_tolerance_nm"] == 10_000
    assert data["arc_step_deg"] == 5.0


# -- what does not close, and what is not counted ---------------------------------


def test_a_missing_side_is_an_open_chain_with_the_gap_that_defeated_it():
    data = outline_data(Board(*square_segments()[:3]))
    assert data["loops"] == []
    [chain] = data["open_chains"]
    assert chain["closed"] is False
    assert chain["source_shapes"] == 3
    assert chain["gap_nm"] == 25 * MM, "100,75 back to 100,50"
    assert len(chain["points_nm"]) == 4


def test_an_open_outline_never_reaches_the_tool_as_a_rectangle():
    board = Board(*square_segments()[:3])
    r = describe_outline(outline_data(board))
    outline = r["outline"]
    assert outline["present"] is True
    assert outline["closed"] is False
    assert outline["loops"] == [] and outline["loop_count"] == 0
    assert outline["open_chains"][0]["gap_mm"] == 25.0
    assert "25.0 mm" in outline["reason"]
    # The extents are reported because they are measured, not assumed.
    assert r["extents"]["width_mm"] == 40.0 and r["extents"]["height_mm"] == 25.0


def test_a_gap_just_outside_the_tolerance_does_not_close():
    gap = 11_000  # 0.011 mm
    shapes = square_segments()
    shapes[0].end = Vector2.from_xy(140 * MM + gap, 50 * MM)
    shapes[1].start = Vector2.from_xy(140 * MM + 2 * gap, 50 * MM)
    data = outline_data(Board(*shapes))
    assert data["loops"] == []
    assert data["open_chains"], "the pieces are reported as they stand"


def test_a_zero_length_segment_is_dropped_rather_than_ending_a_chain():
    shapes = [*square_segments(), seg(120, 50, 120, 50)]
    data = outline_data(Board(*shapes))
    [loop] = data["loops"]
    assert loop["closed"] is True and loop["source_shapes"] == 4
    assert data["open_chains"] == []
    assert data["shape_count"] == 5, "the shape is still counted, just not chained"


def test_the_same_shape_drawn_twice_does_not_double_the_loop():
    shapes = [*square_segments(), seg(100, 50, 140, 50), seg(140, 75, 140, 50)]
    data = outline_data(Board(*shapes))
    [loop] = data["loops"]
    assert loop["closed"] is True
    assert len(loop["points_nm"]) == 4
    assert data["open_chains"] == []


def test_a_footprint_owned_edge_cuts_shape_never_contributes_a_loop():
    board = _fp_edge_board(rect(100, 50, 130, 70))
    data = outline_data(board)
    [loop] = data["loops"]
    assert [(p["x"] / MM, p["y"] / MM) for p in loop["points_nm"]] == [
        (100.0, 50.0),
        (130.0, 50.0),
        (130.0, 70.0),
        (100.0, 70.0),
    ]
    assert data["shape_count"] == 1
    assert data["outline_nm"] == box(100, 50, 130, 70)


def test_only_footprint_edge_cuts_graphics_is_no_outline_and_no_frame():
    data = outline_data(_fp_edge_board())
    assert data["shape_count"] == 0
    assert data["loops"] == [] and data["open_chains"] == []
    r = describe_outline(data)
    assert r["outline"] == {"present": False, "reason": "No board-level shapes on Edge.Cuts."}
    assert r["frame_offset"]["present"] is False
    assert r["extents"] is None


def test_shapes_on_other_layers_contribute_no_loops():
    data = outline_data(
        Board(rect(100, 50, 140, 75), rect(0, 0, 500, 500, layer=BoardLayer.BL_F_SilkS))
    )
    assert len(data["loops"]) == 1
    assert data["outline_nm"] == box(100, 50, 140, 75)


def test_an_unmeasurable_edge_cuts_shape_still_raises_internal_error_here():
    with pytest.raises(OpenKonnectError) as exc:
        outline_data(Board(rect(100, 50, 140, 75), _unknown_shape()))
    assert exc.value.code is ErrorCode.INTERNAL
    assert exc.value.details["shape_id"] == "e5c1-unknown"


# -- origins (LAY-08) -------------------------------------------------------------


def test_both_origins_are_read_from_kicad_and_reported_in_both_frames():
    from kipy.proto.board.board_commands_pb2 import BoardOriginType

    board = Board(rect(100, 50, 140, 75))
    board.origin_at = {
        BoardOriginType.BOT_GRID: v(105, 55),
        BoardOriginType.BOT_DRILL: v(100, 50),
    }
    data = outline_data(board)
    assert board.origins_asked == [BoardOriginType.BOT_GRID, BoardOriginType.BOT_DRILL]
    assert data["grid_origin_nm"] == {"x": 105 * MM, "y": 55 * MM}
    assert data["drill_origin_nm"] == {"x": 100 * MM, "y": 50 * MM}
    assert data["origins_reason"] is None
    r = describe_outline(data)
    assert r["origins"]["grid"]["board"] == {"frame": "board", "x_mm": 5.0, "y_mm": 5.0}
    assert r["origins"]["drill"]["board"] == {"frame": "board", "x_mm": 0.0, "y_mm": 0.0}


def test_a_kicad_that_does_not_implement_the_origin_query_gives_data_not_a_failure():
    from kipy.errors import ApiError
    from kipy.proto.common import ApiStatusCode

    class NoOrigins(Board):
        def get_origin(self, origin_type: Any) -> Vector2:
            raise ApiError("no handler", code=ApiStatusCode.AS_UNIMPLEMENTED)

    data = outline_data(NoOrigins(rect(100, 50, 140, 75)))
    assert data["grid_origin_nm"] is None and data["drill_origin_nm"] is None
    assert "GetBoardOrigin" in data["origins_reason"]
    assert len(data["loops"]) == 1, "the outline is still reported"


def test_a_busy_origin_request_is_raised_so_the_session_can_retry_it():
    from kipy.errors import ApiError

    from .conftest import busy

    class BusyOrigins(Board):
        def get_origin(self, origin_type: Any) -> Vector2:
            raise busy()

    with pytest.raises(ApiError):
        outline_data(BusyOrigins(rect(100, 50, 140, 75)))


# -- through the tool -------------------------------------------------------------


def test_board_outline_over_the_fixture_board_reports_the_geometric_rectangle():
    class Fake(FakeBackend):
        def outline(self, ref: Any) -> dict[str, Any]:
            self.calls.append("outline")
            return outline_data(_fixture_board())

    r = make_session(Fake(open_boards=[[board_ref()]])).run_tool(board_outline)
    assert r["ok"] is True, r
    [loop] = r["outline"]["loops"]
    assert [(p["x_mm"], p["y_mm"]) for p in loop["points_mm"]] == [
        (100.0, 50.0),
        (140.0, 50.0),
        (140.0, 75.0),
        (100.0, 75.0),
    ]
    assert r["frame_offset"]["x_mm"] == 100.0 and r["frame_offset"]["y_mm"] == 50.0
    assert r["extents"]["width_mm"] == 40.0 and r["extents"]["height_mm"] == 25.0
