"""The board outline comes from Edge.Cuts geometry, not KiCad's stroked boxes (#32).

KiCad's ``GetBoundingBox`` for a shape includes its drawn line width, so a
0.1 mm outline stroke moved the board-frame origin by 50 um. These tests build
real kipy shapes on a minimal board fake whose ``get_item_bounding_box``
returns stroke-inflated boxes, so a return to the old union fails here.
"""

from __future__ import annotations

import math
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

from openkonnect.backend import _bezier_extent, _edge_outline
from openkonnect.errors import ErrorCode, OpenKonnectError
from openkonnect.tools.board_summary import board_summary
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


def polygon(points: list[tuple[float, float]], **kw: Any) -> BoardPolygon:
    line = PolyLine()
    for x, y in points:
        line.append(PolyLineNode.from_point(v(x, y)))
    pwh = PolygonWithHoles()
    pwh.outline = line
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
    """The two kipy ``Board`` calls ``_edge_outline`` may use."""

    def __init__(self, *shapes: Any) -> None:
        self.shapes = list(shapes)
        self.requested: list[Any] = []

    def get_items(self, types: Any) -> list[Any]:
        self.requested.append(types)
        return list(self.shapes)

    def get_item_bounding_box(self, items: Any) -> list[Box2]:
        return [_inflated(s) for s in items]

    def get_shapes(self) -> list[Any]:  # kipy drops unclassified shapes here
        return [s for s in self.shapes if type(s) is not BoardShape]


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
