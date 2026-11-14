"""The narrow IPC surface openkonnect needs, and its kicad-python implementation.

The session layer (``session.py``) talks only to :class:`IpcBackend`. The real
implementation, :class:`KipyBackend`, wraps KiCad's official Python bindings
(``kicad-python``, imported as ``kipy``; MIT, see ``docs/ipc-client.md``).
Tests substitute a fake backend that raises the same ``kipy`` exception types.

Backends raise ``kipy.errors.ConnectionError`` and ``kipy.errors.ApiError``
unchanged; ``session.py`` maps them onto C-3 codes.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from .errors import ErrorCode, OpenKonnectError

NM_PER_MM = 1_000_000


@dataclass
class BoardRef:
    """One board open in the PCB editor, as KiCad reports it.

    ``path`` is None for a board that has never been saved (a new board, with
    no file and no project). Tools that need the file refuse it with
    ``no_document``; see :func:`openkonnect.tools.common.require_saved`.
    """

    path: str | None
    file_name: str
    project_dir: str | None = None
    project_name: str | None = None
    handle: Any = field(default=None, repr=False, compare=False)


class IpcBackend(Protocol):
    """What the session layer needs from an IPC client."""

    socket: str

    def ping(self) -> None: ...

    def kicad_version(self) -> str: ...

    def instance_token(self) -> str | None: ...

    def open_boards(self) -> list[BoardRef]: ...

    def board_data(self, ref: BoardRef) -> dict[str, Any]: ...

    def board_text(self, ref: BoardRef) -> str:
        """The open board as KiCad native-format text, including unsaved changes."""
        ...

    def save_board(self, ref: BoardRef) -> None:
        """Ask the PCB editor to save the board to its own file."""
        ...

    def revert_board(self, ref: BoardRef) -> None:
        """Ask the PCB editor to reload the board from its file, dropping unsaved changes."""
        ...

    def selection(self, ref: BoardRef, kinds: list[str] | None = None) -> dict[str, Any]:
        """The editor's current selection (#5), as plain data.

        Returns ``{"items": [item, ...], "undescribed": {type_name: count},
        "outline_nm": box | None}``. Each item is a dict with ``id``, ``kind``
        (one of :data:`SELECTION_KINDS`), ``ref``/``pad`` where the item has a
        file-level name, ``net``, ``layer``, ``position_nm`` and ``bbox_nm``
        (file frame, nm). Items the client cannot decode are counted in
        ``undescribed``, never dropped.
        """
        ...

    def board_index(self, ref: BoardRef) -> dict[str, Any]:
        """Footprints (with their pads), copper items and nets, for resolving references.

        Returns ``{"footprints": [...], "copper": [...], "nets": [...],
        "outline_nm": box | None}``; items use the same dict form as
        :meth:`selection`. A footprint item also carries ``value``, ``library_id``,
        ``orientation_deg``, ``locked``, ``attributes`` and ``fields`` (LAY-02).
        """
        ...

    def nets(self, ref: BoardRef, include_items: bool = False) -> dict[str, Any]:
        """The board's nets (LAY-04), as plain data.

        Returns ``{"nets": [{"name", "net_class", "pad_count", "items"?}, ...]}``.
        ``net_class`` is the name of the net's class (None if KiCad reports none).
        With ``include_items``, ``items`` lists the pads (``kind: "pad"``, ``ref``,
        ``pad``, ``id``) and copper (``kind``, ``id``) on the net.
        """
        ...

    def zones(self, ref: BoardRef) -> dict[str, Any]:
        """Copper zones and rule areas (LAY-06), as plain data.

        Returns ``{"zones": [zone, ...]}``. Each zone has ``id`` (session ID), ``kind``
        (``"copper"``, ``"rule_area"`` or another zone type name), ``name`` (None if
        unnamed), ``net`` (None for a rule area or an unconnected zone), ``layers``
        (canonical names), ``priority``, ``pad_connection`` (``"inherited"``, ``"none"``,
        ``"thermal"``, ``"full"``, ``"pth_thermal"``; None for a rule area), ``filled``,
        ``locked`` and ``keepouts`` (None for a copper zone; for a rule area a dict of
        ``copper``, ``vias``, ``tracks``, ``pads``, ``footprints`` booleans).
        """
        ...

    def outline(self, ref: BoardRef) -> dict[str, Any]:
        """The board outline as polygons, with the extents and KiCad's origins (LAY-08).

        Returns ``{"shape_count", "loops", "open_chains", "outline_nm",
        "grid_origin_nm", "drill_origin_nm", "origins_reason",
        "chain_tolerance_nm", "arc_step_deg"}``.

        ``loops`` are the closed rings, ``open_chains`` the pieces that did not
        close; each is ``{"closed", "role" ("outline" or "hole"),
        "points_nm": [{"x", "y"}, ...], "source_shapes", "gap_nm"}``. A closed
        ring does not repeat its first point and has ``gap_nm`` None; an open
        chain's ``gap_nm`` is the distance from its last point back to its
        first, so a caller can say what defeated the chain. Arcs, beziers and
        circles are sampled into segments at ``arc_step_deg``, so a loop that
        contains one is an approximation. Chaining joins endpoints within
        ``chain_tolerance_nm``.

        ``outline_nm`` is the extents box :meth:`board_data` reports, on the
        same basis (geometry, not stroke; board-level shapes only). The origins
        are ``{"x", "y"}`` in the ``file`` frame, or None with
        ``origins_reason`` set when this KiCad has no origin query.
        """
        ...

    def pads(self, ref: BoardRef, refs: list[str] | None = None) -> dict[str, Any]:
        """Pads with their geometry (LAY-03), as plain data.

        Returns ``{"footprints": [ref, ...], "pads": [pad, ...], "outline_nm": box | None}``.
        ``footprints`` lists every footprint reference on the board. Each pad has ``ref``,
        ``pad`` (number), ``net`` (name, None if unconnected or unreadable), ``net_state``
        (``"connected"``, ``"unconnected"`` or ``"unknown"``), ``net_reason`` when unknown,
        ``position_nm``, ``layers`` (canonical names), ``shape`` and ``size_nm``
        (``{"x", "y"}``; None if KiCad gives no copper layer). With ``refs``, only pads of
        those footprints are returned.
        """
        ...

    def set_selection(self, ref: BoardRef, ids: list[str]) -> None:
        """Replace the editor selection with the items whose session IDs are ``ids``."""
        ...

    def close(self) -> None: ...


#: Item kinds ``selection`` reports, and the filter values ``get_selection`` accepts.
SELECTION_KINDS = (
    "footprint",
    "pad",
    "track",
    "arc",
    "via",
    "zone",
    "shape",
    "text",
    "textbox",
    "field",
    "dimension",
    "barcode",
    "reference_image",
    "group",
)

#: Kinds that carry copper connectivity (a net).
COPPER_KINDS = ("track", "arc", "via", "zone")


def client_api_version() -> str | None:
    """The KiCad API version the installed kicad-python was built against."""
    try:
        from kipy.kicad_api_version import KICAD_API_VERSION
    except Exception:  # pragma: no cover - only if kipy is broken
        return None
    return str(KICAD_API_VERSION)


class KipyBackend:
    """:class:`IpcBackend` over ``kipy.KiCad``. One instance holds one connection."""

    def __init__(self, socket: str, timeout_ms: int, client_name: str = "openkonnect") -> None:
        from kipy import KiCad

        self.socket = socket
        self._kicad = KiCad(socket_path=socket, client_name=client_name, timeout_ms=timeout_ms)

    # -- session -----------------------------------------------------------

    def ping(self) -> None:
        self._kicad.ping()

    def kicad_version(self) -> str:
        v = self._kicad.get_version()
        return v.full_version or f"{v.major}.{v.minor}.{v.patch}"

    def instance_token(self) -> str | None:
        # kipy keeps the token KiCad hands out on first contact; it changes when
        # KiCad restarts. It is not public API, so read it defensively.
        client = getattr(self._kicad, "_client", None)
        token = getattr(client, "_kicad_token", None)
        return token or None

    def open_boards(self) -> list[BoardRef]:
        from kipy.errors import ApiError
        from kipy.proto.common import ApiStatusCode
        from kipy.proto.common.types import DocumentType

        try:
            docs = self._kicad.get_open_documents(DocumentType.DOCTYPE_PCB)
        except ApiError as e:
            # With no PCB editor frame open, KiCad has no handler for board
            # requests. That is "no board open", not a failure.
            if e.code == ApiStatusCode.AS_UNHANDLED:
                return []
            raise
        refs = []
        for doc in docs:
            project_dir = doc.project.path or None
            project_name = doc.project.name or None
            file_name = doc.board_filename
            if not file_name:
                if project_dir:
                    # For a few hundred ms after the PCB Editor starts, KiCad
                    # 10.0.7 lists the board with an empty board_filename but a
                    # populated project path (#34; docs/ipc-client.md, "Startup
                    # window"). Joining them would give the project directory
                    # as the board path. Treat it as "not ready" so Session.call
                    # retries it and ends in a retryable kicad_busy if it never
                    # clears.
                    raise ApiError(
                        "board document not fully loaded "
                        f"(board_filename='', project.path={project_dir!r})",
                        code=ApiStatusCode.AS_NOT_READY,
                    )
                # No filename and no project: a new board that has never been
                # saved. KiCad keeps answering this way, so it is not a startup
                # state to retry. Report the board with no path.
                refs.append(BoardRef(None, "", None, project_name, handle=doc))
                continue
            # Any other non-empty name is reported as KiCad gives it, joined to
            # the project directory when relative. Only "" was ever observed to
            # mean "not loaded yet" (#34), so no other name is retried.
            path = file_name
            if project_dir and not os.path.isabs(file_name):
                path = os.path.join(project_dir, file_name)
            refs.append(BoardRef(path, file_name, project_dir, project_name, handle=doc))
        return refs

    def close(self) -> None:
        client = getattr(self._kicad, "_client", None)
        conn = getattr(client, "_conn", None)
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()

    # -- board -------------------------------------------------------------

    def _board(self, ref: BoardRef) -> Any:
        from kipy.board import Board

        return Board(self._kicad._client, ref.handle)

    # File sync (#4). kicad-python 0.8 documents these three Board methods,
    # each one IPC request, with no KiCad 11 restriction:
    #   Board.get_as_string() -> SaveDocumentToString
    #   Board.save()          -> SaveDocument
    #   Board.revert()        -> RevertDocument
    # There is no "modified since last save" query in 0.8 (KiCad 11 only).

    def board_text(self, ref: BoardRef) -> str:
        return self._board(ref).get_as_string()

    def save_board(self, ref: BoardRef) -> None:
        self._board(ref).save()

    def revert_board(self, ref: BoardRef) -> None:
        self._board(ref).revert()

    def board_data(self, ref: BoardRef) -> dict[str, Any]:
        from kipy.board_types import ArcTrack
        from kipy.proto.board.board_types_pb2 import ZoneType

        board = self._board(ref)

        tb = board.get_title_block_info()
        title_block = {
            "title": tb.title,
            "date": tb.date,
            "revision": tb.revision,
            "company": tb.company,
        }

        tracks = board.get_tracks()
        arcs = sum(1 for t in tracks if isinstance(t, ArcTrack))
        zones = board.get_zones()
        nets = [n for n in board.get_nets() if n.name]

        edge, outline = _edge_outline(board)

        return {
            "title_block": title_block,
            "copper_layers": board.get_copper_layer_count(),
            "enabled_layers": len(board.get_enabled_layers()),
            "footprints": len(board.get_footprints()),
            "nets": len(nets),
            "track_segments": len(tracks) - arcs,
            "track_arcs": arcs,
            "vias": len(board.get_vias()),
            "zones": len(zones),
            "rule_areas": sum(1 for z in zones if z.type == ZoneType.ZT_RULE_AREA),
            "edge_cuts_shapes": len(edge),
            "outline_nm": outline,
        }

    # Selection (#5); the bodies are below the class, see the note there.

    def selection(self, ref: BoardRef, kinds: list[str] | None = None) -> dict[str, Any]:
        return _selection(self, ref, kinds)

    def board_index(self, ref: BoardRef) -> dict[str, Any]:
        return _board_index(self, ref)

    def nets(self, ref: BoardRef, include_items: bool = False) -> dict[str, Any]:
        return _nets(self, ref, include_items)

    def zones(self, ref: BoardRef) -> dict[str, Any]:
        return _zones(self, ref)

    def outline(self, ref: BoardRef) -> dict[str, Any]:
        return _outline(self, ref)

    def pads(self, ref: BoardRef, refs: list[str] | None = None) -> dict[str, Any]:
        return _pads(self, ref, refs)

    def set_selection(self, ref: BoardRef, ids: list[str]) -> None:
        _set_selection(self, ref, ids)


def _edge_outline(board: Any) -> tuple[list[Any], dict[str, int] | None]:
    """Board-level Edge.Cuts shapes and the box of their geometry (file frame, nm).

    The box is the shapes' own geometry (corners, endpoints, arc, circle and
    curve extents), not ``get_item_bounding_box``: KiCad's box includes the
    drawn line width, which moved the board-frame origin by half a stroke (#32).

    ``Board.get_shapes`` would give the same shapes (kipy 0.8.0 keeps an
    unclassified shape as a bare ``BoardShape``); ``get_items`` is used so that
    shape is visible here, and an Edge.Cuts shape that is not one of the six
    kipy shape classes raises ``internal_error`` instead of being skipped, so
    the outline is never silently wrong.

    Only board-level Edge.Cuts graphics count; footprint-owned ones are
    excluded to match ``kct``'s board origin (C-1, #41). No filter is needed:
    KiCad 10.0.7 does not return footprint children from board-level
    ``GetItems(KOT_PCB_SHAPE)`` (they come only through
    ``FootprintInstance.definition``; docs/ipc-client.md).
    """
    from kipy.board_types import to_concrete_board_shape
    from kipy.proto.board.board_types_pb2 import BoardLayer
    from kipy.proto.common.types import KiCadObjectType

    edge = [
        to_concrete_board_shape(s)
        for s in board.get_items([KiCadObjectType.KOT_PCB_SHAPE])
        if s.layer == BoardLayer.BL_Edge_Cuts
    ]
    extents = [e for e in (_shape_extent(s) for s in edge) if e is not None]
    outline = None
    if extents:
        outline = {
            "x0_nm": min(e[0] for e in extents),
            "y0_nm": min(e[1] for e in extents),
            "x1_nm": max(e[2] for e in extents),
            "y1_nm": max(e[3] for e in extents),
        }
    return edge, outline


Extent = tuple[int, int, int, int]  # x0, y0, x1, y1 in nm


def _points_extent(points: list[Any]) -> Extent:
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _box_extent(box: Any) -> Extent:
    return box.pos.x, box.pos.y, box.pos.x + box.size.x, box.pos.y + box.size.y


def _shape_extent(shape: Any) -> Extent | None:
    """One Edge.Cuts shape's geometric extent, without stroke width.

    ``None`` only for a polygon with no outline points (it bounds nothing).
    """
    from kipy.board_types import (
        BoardArc,
        BoardBezier,
        BoardCircle,
        BoardPolygon,
        BoardRectangle,
        BoardSegment,
    )
    from kipy.geometry import arc_bounding_box

    # Exact type checks: kipy leaves an unclassified shape as a bare BoardShape.
    kind = type(shape)
    if kind is BoardRectangle:
        return _points_extent([shape.top_left, shape.bottom_right])
    if kind is BoardSegment:
        return _points_extent([shape.start, shape.end])
    if kind is BoardArc:
        return _box_extent(arc_bounding_box(shape.start, shape.mid, shape.end))
    if kind is BoardCircle:
        return _box_extent(shape.bounding_box())
    if kind is BoardBezier:
        return _bezier_extent(shape.start, shape.control1, shape.control2, shape.end)
    if kind is BoardPolygon:
        extents: list[Extent] = []
        for poly in shape.polygons:  # holes lie inside their outline
            for node in poly.outline:
                if node.has_point:
                    extents.append(_points_extent([node.point]))
                elif node.has_arc:
                    a = node.arc
                    extents.append(_box_extent(arc_bounding_box(a.start, a.mid, a.end)))
        if not extents:
            return None
        return (
            min(e[0] for e in extents),
            min(e[1] for e in extents),
            max(e[2] for e in extents),
            max(e[3] for e in extents),
        )
    proto = getattr(shape, "_proto", None)
    geometry = None
    with contextlib.suppress(Exception):
        geometry = proto.shape.WhichOneof("geometry")  # type: ignore[union-attr]
    shape_id = None
    with contextlib.suppress(Exception):
        shape_id = shape.id.value or None
    raise OpenKonnectError(
        ErrorCode.INTERNAL,
        "An Edge.Cuts shape has a geometry openkonnect cannot measure, so the board "
        "outline (and the board frame) cannot be computed. Report it with these details.",
        details={"shape_id": shape_id, "geometry": geometry, "class": kind.__name__},
    )


def _bezier_extent(p0: Any, p1: Any, p2: Any, p3: Any) -> Extent:
    """Extent of a cubic Bezier: its endpoints plus its interior extrema per axis.

    B'(t) is proportional to (a - 2b + c) t^2 + 2 (b - a) t + a with
    a = P1 - P0, b = P2 - P1, c = P3 - P2; its roots in (0, 1) are the extrema.
    The control points themselves only bound the curve, so they are not used.
    """

    def axis(v0: float, v1: float, v2: float, v3: float) -> tuple[int, int]:
        a, b, c = v1 - v0, v2 - v1, v3 - v2
        qa, qb, qc = a - 2 * b + c, 2 * (b - a), a
        ts: list[float] = []
        if qa == 0:
            if qb != 0:
                ts.append(-qc / qb)
        else:
            disc = qb * qb - 4 * qa * qc
            if disc >= 0:
                r = disc**0.5
                ts += [(-qb + r) / (2 * qa), (-qb - r) / (2 * qa)]
        values = [float(v0), float(v3)]
        for t in ts:
            if 0 < t < 1:
                u = 1 - t
                values.append(u**3 * v0 + 3 * u * u * t * v1 + 3 * u * t * t * v2 + t**3 * v3)
        return round(min(values)), round(max(values))

    x0, x1 = axis(p0.x, p1.x, p2.x, p3.x)
    y0, y1 = axis(p0.y, p1.y, p2.y, p3.y)
    return x0, y0, x1, y1


# -- board outline polygons (LAY-08, #88) ------------------------------------
#
# kipy 0.8.0 has no board-outline helper: ``Board`` offers no
# ``get_board_outline()`` and ``kipy.geometry`` only measures single arcs. The
# chaining below is therefore ours. ``kicad-tools``' two implementations were
# read and deliberately not reused: both fall back to a bounding box (one to a
# hardcoded 100 x 100 mm board) when chaining fails, which LAY-08 forbids, and
# both read the sexp file rather than the open editor (C-7). Their greedy
# endpoint matching and 0.01 mm tolerance are the reference for the tolerance
# below.

#: Endpoints this far apart or closer are the same point when chaining (0.01 mm).
#: The value ``kct``'s ``_chain_lines_to_polygon`` uses, so the two agree about
#: which outlines close.
_CHAIN_TOLERANCE_NM = 10_000

#: Sampling step for arcs and circles. Curves become polylines because LAY-08
#: asks for polygons; the step is reported so a consumer knows the loop is an
#: approximation.
_ARC_STEP_DEG = 5.0

#: Segments a cubic bezier is sampled into (beziers have no angular measure).
_BEZIER_STEPS = 24


def _pt(p: Any) -> dict[str, int]:
    return {"x": int(p.x), "y": int(p.y)}


def _near(a: dict[str, int], b: dict[str, int], tol: int = _CHAIN_TOLERANCE_NM) -> bool:
    return abs(a["x"] - b["x"]) <= tol and abs(a["y"] - b["y"]) <= tol


def _gap_nm(a: dict[str, int], b: dict[str, int]) -> int:
    return round(((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5)


def _arc_points(start: Any, mid: Any, end: Any) -> list[dict[str, int]]:
    """An arc sampled into points, start and end kept exact.

    The sweep is the one that passes through ``mid``, so the direction KiCad
    drew is preserved. A degenerate arc (no center) falls back to its three
    defining points rather than being dropped.
    """
    import math

    from kipy.geometry import arc_center, arc_radius

    first, last = _pt(start), _pt(end)
    center = arc_center(start, mid, end)
    if center is None:
        return [first, _pt(mid), last]
    radius = arc_radius(start, mid, end)
    if not radius:
        return [first, _pt(mid), last]
    cx, cy = float(center.x), float(center.y)

    def angle(p: Any) -> float:
        return math.atan2(float(p.y) - cy, float(p.x) - cx)

    a0 = angle(start)
    turn = math.tau
    to_mid = (angle(mid) - a0) % turn
    to_end = (angle(end) - a0) % turn
    if to_end == 0.0:
        sweep = turn if to_mid > 0 else -turn  # start == end: a full circle
    elif to_mid <= to_end:
        sweep = to_end
    else:
        sweep = to_end - turn
    steps = max(2, math.ceil(abs(sweep) / math.radians(_ARC_STEP_DEG)))
    points = [first]
    for i in range(1, steps):
        t = a0 + sweep * i / steps
        points.append(
            {"x": round(cx + radius * math.cos(t)), "y": round(cy + radius * math.sin(t))}
        )
    points.append(last)
    return points


def _circle_points(center: Any, radius: float) -> list[dict[str, int]]:
    """A circle sampled into a closed ring (the first point is not repeated)."""
    import math

    steps = max(8, math.ceil(360.0 / _ARC_STEP_DEG))
    cx, cy = float(center.x), float(center.y)
    return [
        {
            "x": round(cx + radius * math.cos(math.tau * i / steps)),
            "y": round(cy + radius * math.sin(math.tau * i / steps)),
        }
        for i in range(steps)
    ]


def _bezier_points(p0: Any, p1: Any, p2: Any, p3: Any) -> list[dict[str, int]]:
    """A cubic bezier sampled into points, start and end kept exact."""
    points = [_pt(p0)]
    for i in range(1, _BEZIER_STEPS):
        t = i / _BEZIER_STEPS
        u = 1 - t
        points.append(
            {
                "x": round(u**3 * p0.x + 3 * u * u * t * p1.x + 3 * u * t * t * p2.x + t**3 * p3.x),
                "y": round(u**3 * p0.y + 3 * u * u * t * p1.y + 3 * u * t * t * p2.y + t**3 * p3.y),
            }
        )
    points.append(_pt(p3))
    return points


def _polyline_points(line: Any) -> list[dict[str, int]]:
    """A kipy ``PolyLine``'s nodes as points; arc nodes are sampled."""
    points: list[dict[str, int]] = []
    for node in line:
        if node.has_point:
            piece = [_pt(node.point)]
        elif node.has_arc:
            a = node.arc
            piece = _arc_points(a.start, a.mid, a.end)
        else:  # pragma: no cover - kipy sets one of the two
            continue
        for p in piece:
            if not points or not _near(points[-1], p, 0):
                points.append(p)
    while len(points) > 1 and _near(points[0], points[-1]):
        points.pop()  # a polyline KiCad closed explicitly; rings do not repeat
    return points


def _rectangle_points(shape: Any) -> list[dict[str, int]]:
    """A rectangle's four corners, clockwise from the top left (corners may be unordered)."""
    xs = sorted((int(shape.top_left.x), int(shape.bottom_right.x)))
    ys = sorted((int(shape.top_left.y), int(shape.bottom_right.y)))
    return [
        {"x": xs[0], "y": ys[0]},
        {"x": xs[1], "y": ys[0]},
        {"x": xs[1], "y": ys[1]},
        {"x": xs[0], "y": ys[1]},
    ]


def _shape_id(shape: Any) -> str:
    with contextlib.suppress(Exception):
        return str(shape.id.value or "")
    return ""  # pragma: no cover - kipy always has an id field


def _loop(points: list[dict[str, int]], role: str, shapes: int) -> dict[str, Any]:
    return {
        "closed": True,
        "role": role,
        "points_nm": points,
        "source_shapes": shapes,
        "gap_nm": None,
    }


def _classify_shapes(edge: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split Edge.Cuts shapes into rings that close on their own and open pieces.

    A rectangle, a circle and each ring of a polygon are already closed, so they
    are loops in their own right; a polygon's holes stay separate loops rather
    than being merged into its outline. A segment, arc or bezier is an open
    piece for :func:`_chain_pieces`. An unclassifiable shape reaches
    :func:`_shape_extent`, which raises ``internal_error`` (``_edge_outline``
    has already done so; this keeps the two paths honest together).
    """
    from kipy.board_types import (
        BoardArc,
        BoardBezier,
        BoardCircle,
        BoardPolygon,
        BoardRectangle,
        BoardSegment,
    )

    rings: list[dict[str, Any]] = []
    pieces: list[dict[str, Any]] = []
    for shape in edge:
        kind = type(shape)
        if kind is BoardRectangle:
            rings.append(_loop(_rectangle_points(shape), "outline", 1))
        elif kind is BoardCircle:
            rings.append(_loop(_circle_points(shape.center, float(shape.radius())), "outline", 1))
        elif kind is BoardPolygon:
            for poly in shape.polygons:
                points = _polyline_points(poly.outline)
                if len(points) >= 3:
                    rings.append(_loop(points, "outline", 1))
                for hole in poly.holes:
                    hole_points = _polyline_points(hole)
                    if len(hole_points) >= 3:
                        rings.append(_loop(hole_points, "hole", 1))
        elif kind is BoardSegment:
            pieces.append({"points": [_pt(shape.start), _pt(shape.end)], "id": _shape_id(shape)})
        elif kind is BoardArc:
            pieces.append(
                {"points": _arc_points(shape.start, shape.mid, shape.end), "id": _shape_id(shape)}
            )
        elif kind is BoardBezier:
            pieces.append(
                {
                    "points": _bezier_points(
                        shape.start, shape.control1, shape.control2, shape.end
                    ),
                    "id": _shape_id(shape),
                }
            )
        else:
            _shape_extent(shape)  # raises internal_error, naming the shape
    return rings, pieces


def _piece_key(piece: dict[str, Any]) -> tuple[Any, ...]:
    """A deterministic order for chaining, from geometry then id.

    Direction-independent, so the same board gives the same loops whatever
    order KiCad lists the shapes in, and a junction where three or more
    endpoints coincide is resolved the same way on every call.
    """
    a = (piece["points"][0]["x"], piece["points"][0]["y"])
    b = (piece["points"][-1]["x"], piece["points"][-1]["y"])
    lo, hi = sorted((a, b))
    return (lo, hi, len(piece["points"]), piece["id"])


def _prepare_pieces(pieces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop zero-length pieces and exact duplicates, then order deterministically.

    A zero-length segment would otherwise end a chain at a point it cannot
    leave, and a shape drawn twice would double its loop.
    """
    kept: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for piece in sorted(pieces, key=_piece_key):
        points = piece["points"]
        if _near(points[0], points[-1]) and len(points) <= 2:
            continue  # zero-length: nothing to chain through
        fingerprint = tuple((p["x"], p["y"]) for p in points)
        if fingerprint in seen or tuple(reversed(fingerprint)) in seen:
            continue
        seen.add(fingerprint)
        kept.append(piece)
    return kept


def _chain_pieces(
    pieces: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Greedy endpoint chaining into closed loops and leftover open chains.

    Each chain grows from both ends so a piece in the middle of a run still
    completes, and candidates are tried in :func:`_piece_key` order so the
    result does not depend on the order KiCad listed the shapes. A chain whose
    ends meet within :data:`_CHAIN_TOLERANCE_NM` is a closed loop; one that
    does not is returned as it stands, with the gap that defeated it. Several
    closed loops are normal: a board with a cutout has two.
    """
    remaining = _prepare_pieces(pieces)
    loops: list[dict[str, Any]] = []
    open_chains: list[dict[str, Any]] = []
    while remaining:
        head = remaining.pop(0)
        points = list(head["points"])
        used = 1
        growing = True
        while growing and not (len(points) >= 4 and _near(points[0], points[-1])):
            growing = False
            for i, piece in enumerate(remaining):
                other = piece["points"]
                if _near(points[-1], other[0]):
                    points.extend(other[1:])
                elif _near(points[-1], other[-1]):
                    points.extend(reversed(other[:-1]))
                elif _near(points[0], other[-1]):
                    points[:0] = other[:-1]
                elif _near(points[0], other[0]):
                    points[:0] = list(reversed(other[1:]))
                else:
                    continue
                remaining.pop(i)
                used += 1
                growing = True
                break
        if len(points) >= 4 and _near(points[0], points[-1]):
            loops.append(_loop(points[:-1], "outline", used))
        else:
            open_chains.append(
                {
                    "closed": False,
                    "role": "outline",
                    "points_nm": points,
                    "source_shapes": used,
                    "gap_nm": _gap_nm(points[0], points[-1]),
                }
            )
    return loops, open_chains


def _origins(board: Any) -> tuple[dict[str, int] | None, dict[str, int] | None, str | None]:
    """KiCad's grid and drill/place origins (file frame, nm), or why they are absent.

    ``Board.get_origin`` is kipy 0.3.0+ and KiCad answers it on 10.0. Only an
    "KiCad does not implement this" reply is turned into data; a busy or timed
    out request is raised so ``session.py`` can retry and map it (C-3).
    """
    from kipy.errors import ApiError
    from kipy.proto.board.board_commands_pb2 import BoardOriginType
    from kipy.proto.common import ApiStatusCode

    try:
        grid = _pt(board.get_origin(BoardOriginType.BOT_GRID))
        drill = _pt(board.get_origin(BoardOriginType.BOT_DRILL))
    except ApiError as e:
        if e.code in (ApiStatusCode.AS_UNIMPLEMENTED, ApiStatusCode.AS_UNHANDLED):
            return (
                None,
                None,
                "This KiCad does not answer GetBoardOrigin, so the grid and drill "
                f"origins are unknown ({ApiStatusCode.Name(e.code)}).",
            )
        raise
    return grid, drill, None


def _outline(self: KipyBackend, ref: BoardRef) -> dict[str, Any]:
    board = self._board(ref)
    edge, extents = _edge_outline(board)
    rings, pieces = _classify_shapes(edge)
    chained, open_chains = _chain_pieces(pieces)
    grid, drill, reason = _origins(board)
    return {
        "shape_count": len(edge),
        "loops": rings + chained,
        "open_chains": open_chains,
        "outline_nm": extents,
        "grid_origin_nm": grid,
        "drill_origin_nm": drill,
        "origins_reason": reason,
        "chain_tolerance_nm": _CHAIN_TOLERANCE_NM,
        "arc_step_deg": _ARC_STEP_DEG,
    }


# -- selection (#5) ----------------------------------------------------------
#
# Verified against kicad-python 0.8.0's own source (MIT):
#   Board.get_selection(types)   -> editor GetSelection / SelectionResponse
#   Board.clear_selection()      -> editor ClearSelection
#   Board.add_to_selection(items)-> editor AddToSelection (sends item ids)
#   Board.get_items_by_id(ids)   -> GetItemsById (KiCad 10.0.0)
#   Board.get_item_bounding_box  -> editor GetBoundingBox
# kipy's get_selection unwraps every item with an assert, so one item of a
# kind the client does not know fails the whole call. ``_read_selection``
# sends the same GetSelection request and unwraps item by item instead, so
# such items are counted as undescribed rather than failing or vanishing.
# kipy's list form of get_item_bounding_box drops items without a box and
# so loses the item-to-box pairing; ``_boxes`` sends the same GetBoundingBox
# request and keeps the pairing from the response's ``items`` field.
# No zoom, view or highlight request is used: kicad-python 0.8 documents none
# (only KiCad.run_action, marked unstable), see spec SES-14/15.


def _kind_types() -> dict[str, Any]:
    from kipy.proto.common.types import KiCadObjectType as T

    return {
        "footprint": T.KOT_PCB_FOOTPRINT,
        "pad": T.KOT_PCB_PAD,
        "track": T.KOT_PCB_TRACE,
        "arc": T.KOT_PCB_ARC,
        "via": T.KOT_PCB_VIA,
        "zone": T.KOT_PCB_ZONE,
        "shape": T.KOT_PCB_SHAPE,
        "text": T.KOT_PCB_TEXT,
        "textbox": T.KOT_PCB_TEXTBOX,
        "field": T.KOT_PCB_FIELD,
        "dimension": T.KOT_PCB_DIMENSION,
        "barcode": T.KOT_PCB_BARCODE,
        "reference_image": T.KOT_PCB_REFERENCE_IMAGE,
        "group": T.KOT_PCB_GROUP,
    }


def _kind_of(item: Any) -> str | None:
    from kipy import board_types as bt

    for cls, kind in (
        (bt.FootprintInstance, "footprint"),
        (bt.Pad, "pad"),
        (bt.ArcTrack, "arc"),
        (bt.Track, "track"),
        (bt.Via, "via"),
        (bt.Zone, "zone"),
        (bt.BoardShape, "shape"),
        (bt.BoardTextBox, "textbox"),
        (bt.BoardText, "text"),
        (bt.Field, "field"),
        (bt.Dimension, "dimension"),
        (bt.Barcode, "barcode"),
        (bt.ReferenceImage, "reference_image"),
        (bt.Group, "group"),
    ):
        if isinstance(item, cls):
            return kind
    return None


def _layer_name(layer: Any) -> str | None:
    from kipy.util.board_layer import canonical_name

    name = canonical_name(layer)
    return None if name == "Unknown" else name


def _vec(v: Any) -> dict[str, int]:
    return {"x": int(v.x), "y": int(v.y)}


def _net_name(net: Any) -> str | None:
    return (net.name or None) if net is not None else None


def _footprint_ref(fp: Any) -> str:
    return fp.reference_field.text.value


def _item_id(item: Any) -> str | None:
    """A board item's KIID as a string; fields have no KIID of their own."""
    from kipy.board_types import Field

    if isinstance(item, Field):
        return None
    value = getattr(item.id, "value", None)
    return value or None


def _boxes(kicad_client: Any, doc: Any, items: list[Any]) -> dict[str, dict[str, int]]:
    from kipy.proto.common.commands import editor_commands_pb2 as ed

    with_ids = [i for i in items if _item_id(i)]
    if not with_ids:
        return {}
    cmd = ed.GetBoundingBox()
    cmd.header.document.CopyFrom(doc)
    cmd.mode = ed.BoundingBoxMode.BBM_ITEM_ONLY
    cmd.items.extend([i.id for i in with_ids])
    resp = kicad_client.send(cmd, ed.GetBoundingBoxResponse)
    out = {}
    for kiid, box in zip(resp.items, resp.boxes, strict=False):
        x, y = int(box.position.x_nm), int(box.position.y_nm)
        out[kiid.value] = {
            "x0_nm": x,
            "y0_nm": y,
            "x1_nm": x + int(box.size.x_nm),
            "y1_nm": y + int(box.size.y_nm),
        }
    return out


def _pad_owners(footprints: list[Any]) -> dict[str, tuple[str, str]]:
    """Pad KIID -> (footprint reference, pad number), from footprint definitions."""
    owners: dict[str, tuple[str, str]] = {}
    for fp in footprints:
        ref = _footprint_ref(fp)
        for pad in fp.definition.pads:
            if pad.id.value:
                owners[pad.id.value] = (ref, pad.number)
    return owners


def _footprint_details(fp: Any) -> dict[str, Any]:
    """The LAY-02 extras of a footprint: value, library ID, rotation, lock, attributes, fields."""
    from kipy.board_types import Field
    from kipy.proto.board import board_types_pb2 as bt

    lib = fp.definition.id
    library_id = f"{lib.library}:{lib.name}" if lib.library else (lib.name or None)
    attrs = fp.attributes
    mounting = {
        bt.FootprintMountingStyle.FMS_SMD: "smd",
        bt.FootprintMountingStyle.FMS_THROUGH_HOLE: "through_hole",
    }.get(attrs.mounting_style)
    fields: dict[str, str] = {}
    for f in (*fp.definition.items, fp.datasheet_field, fp.description_field):
        if isinstance(f, Field) and f.name and f.text.value:
            fields[f.name] = f.text.value
    return {
        "value": fp.value_field.text.value,
        "library_id": library_id,
        "orientation_deg": round(fp.orientation.degrees, 6),
        "locked": bool(fp.locked),
        "attributes": {
            "do_not_populate": bool(attrs.do_not_populate),
            "exclude_from_bom": bool(attrs.exclude_from_bill_of_materials),
            "exclude_from_position_files": bool(attrs.exclude_from_position_files),
            "not_in_schematic": bool(attrs.not_in_schematic),
            "mounting": mounting,
        },
        "fields": fields,
    }


def _describe(item: Any, kind: str, owners: dict[str, tuple[str, str]]) -> dict[str, Any]:
    d: dict[str, Any] = {"id": _item_id(item), "kind": kind, "net": None, "layer": None}
    if kind == "footprint":
        d["ref"] = _footprint_ref(item)
        d["layer"] = _layer_name(item.layer)
        d["position_nm"] = _vec(item.position)
        d["pads"] = [
            {"id": p.id.value or None, "number": p.number, "net": _net_name(p.net)}
            for p in item.definition.pads
        ]
        d.update(_footprint_details(item))
    elif kind == "pad":
        owner = owners.get(d["id"] or "")
        if owner is not None:
            d["ref"], d["pad"] = owner
        d["net"] = _net_name(item.net)
        d["position_nm"] = _vec(item.position)
    elif kind in ("track", "arc"):
        d["net"] = _net_name(item.net)
        d["layer"] = _layer_name(item.layer)
    elif kind == "via":
        d["net"] = _net_name(item.net)
        d["position_nm"] = _vec(item.position)
    elif kind == "zone":
        d["net"] = _net_name(item.net)
        d["layers"] = [n for n in (_layer_name(lyr) for lyr in item.layers) if n]
        d["name"] = item.name or None
    elif kind == "field":
        d["name"] = item.name
        d["layer"] = _layer_name(item.layer)
    elif kind == "group":
        d["name"] = item.name or None
    else:
        layer = getattr(item, "layer", None)
        d["layer"] = _layer_name(layer) if layer is not None else None
        if kind == "shape":
            d["net"] = _net_name(item.net)
    return d


def _read_selection(
    kicad_client: Any, doc: Any, types: list[Any]
) -> tuple[list[Any], dict[str, int]]:
    from kipy.board_types import (
        BoardShape,
        Dimension,
        to_concrete_board_shape,
        to_concrete_dimension,
        unwrap,
    )
    from kipy.proto.common.commands import editor_commands_pb2 as ed

    cmd = ed.GetSelection()
    cmd.header.document.CopyFrom(doc)
    cmd.types.extend(types)
    resp = kicad_client.send(cmd, ed.SelectionResponse)
    items: list[Any] = []
    undescribed: dict[str, int] = {}
    for raw in resp.items:
        name = raw.type_url.rsplit("/", 1)[-1] or "unknown"
        try:
            item = unwrap(raw)
        except (AssertionError, NotImplementedError, ValueError):
            undescribed[name] = undescribed.get(name, 0) + 1
            continue
        if isinstance(item, BoardShape):
            item = to_concrete_board_shape(item)
        elif isinstance(item, Dimension):
            item = to_concrete_dimension(item)
        if _kind_of(item) is None:
            undescribed[name] = undescribed.get(name, 0) + 1
            continue
        items.append(item)
    return items, undescribed


def _selection(self: KipyBackend, ref: BoardRef, kinds: list[str] | None = None) -> dict[str, Any]:
    board = self._board(ref)
    client = self._kicad._client
    table = _kind_types()
    types = [table[k] for k in (kinds or [])]
    items, undescribed = _read_selection(client, ref.handle, types)
    owners = (
        _pad_owners(list(board.get_footprints()))
        if any(_kind_of(i) == "pad" for i in items)
        else {}
    )
    boxes = _boxes(client, ref.handle, items)
    described = []
    for item in items:
        d = _describe(item, _kind_of(item) or "unknown", owners)
        d["bbox_nm"] = boxes.get(d["id"] or "")
        described.append(d)
    _, outline = _edge_outline(board)
    return {"items": described, "undescribed": undescribed, "outline_nm": outline}


def _board_index(self: KipyBackend, ref: BoardRef) -> dict[str, Any]:
    board = self._board(ref)
    client = self._kicad._client
    footprints = list(board.get_footprints())
    pads = list(board.get_pads())
    copper = [*board.get_tracks(), *board.get_vias(), *board.get_zones()]
    owners = _pad_owners(footprints)
    boxes = _boxes(client, ref.handle, [*footprints, *pads, *copper])
    fps = []
    by_fp: dict[str, list[dict[str, Any]]] = {}
    for p in pads:
        d = _describe(p, "pad", owners)
        d["bbox_nm"] = boxes.get(d["id"] or "")
        if "ref" in d:
            by_fp.setdefault(d["ref"], []).append(d)
    for fp in footprints:
        d = _describe(fp, "footprint", owners)
        d["bbox_nm"] = boxes.get(d["id"] or "")
        d["pads"] = by_fp.get(d["ref"], [])
        fps.append(d)
    cu = []
    for item in copper:
        d = _describe(item, _kind_of(item) or "unknown", owners)
        d["bbox_nm"] = boxes.get(d["id"] or "")
        cu.append(d)
    nets = [n.name for n in board.get_nets() if n.name]
    _, outline = _edge_outline(board)
    return {"footprints": fps, "copper": cu, "nets": nets, "outline_nm": outline}


def _nets(self: KipyBackend, ref: BoardRef, include_items: bool = False) -> dict[str, Any]:
    board = self._board(ref)
    nets = [n for n in board.get_nets() if n.name]
    classes = board.get_netclass_for_nets(nets) if nets else {}
    pads = list(board.get_pads())
    pad_counts: dict[str, int] = {}
    for p in pads:
        name = _net_name(p.net)
        if name:
            pad_counts[name] = pad_counts.get(name, 0) + 1
    items: dict[str, list[dict[str, Any]]] = {}
    if include_items:
        owners = _pad_owners(list(board.get_footprints()))
        for p in pads:
            d = _describe(p, "pad", owners)
            if d["net"]:
                items.setdefault(d["net"], []).append(
                    {k: d[k] for k in ("id", "kind", "ref", "pad") if k in d}
                )
        for c in [*board.get_tracks(), *board.get_vias(), *board.get_zones()]:
            net = _net_name(c.net)
            if net:
                items.setdefault(net, []).append({"id": _item_id(c), "kind": _kind_of(c)})
    out = []
    for n in nets:
        cls = classes.get(n.name)
        entry: dict[str, Any] = {
            "name": n.name,
            "net_class": (cls.name or None) if cls is not None else None,
            "pad_count": pad_counts.get(n.name, 0),
        }
        if include_items:
            entry["items"] = items.get(n.name, [])
        out.append(entry)
    return {"nets": out}


def _zones(self: KipyBackend, ref: BoardRef) -> dict[str, Any]:
    from kipy.proto.board.board_types_pb2 import ZoneConnectionStyle, ZoneType

    board = self._board(ref)
    out = []
    for z in board.get_zones():
        rule_area = z.type == ZoneType.ZT_RULE_AREA
        keepouts = pad_connection = None
        if rule_area:
            ra = z.proto.rule_area_settings
            keepouts = {
                "copper": bool(ra.keepout_copper),
                "vias": bool(ra.keepout_vias),
                "tracks": bool(ra.keepout_tracks),
                "pads": bool(ra.keepout_pads),
                "footprints": bool(ra.keepout_footprints),
            }
        else:
            conn = z.connection
            if conn is not None:
                pad_connection = (
                    ZoneConnectionStyle.Name(conn.zone_connection).removeprefix("ZCS_").lower()
                )
        kind = (
            "copper"
            if z.type == ZoneType.ZT_COPPER
            else "rule_area"
            if rule_area
            else ZoneType.Name(z.type).removeprefix("ZT_").lower()
        )
        out.append(
            {
                "id": _item_id(z),
                "kind": kind,
                "name": z.name or None,
                "net": None if rule_area else _net_name(z.net),
                "layers": [n for n in (_layer_name(lyr) for lyr in z.layers) if n],
                "priority": int(z.priority),
                "pad_connection": pad_connection,
                "filled": bool(z.filled),
                "locked": bool(z.locked),
                "keepouts": keepouts,
            }
        )
    return {"zones": out}


def _pad_geometry(pad: Any) -> dict[str, Any]:
    """Layers, shape and size of a pad, from its padstack (the first copper layer entry)."""
    from kipy.proto.board.board_types_pb2 import PadStackShape

    stack = pad.padstack
    layers = [n for n in (_layer_name(lyr) for lyr in stack.layers) if n]
    shape = size = None
    copper = stack.copper_layers
    if copper:
        shape = PadStackShape.Name(copper[0].shape).removeprefix("PSS_").lower()
        size = {"x": int(copper[0].size.x), "y": int(copper[0].size.y)}
    return {"layers": layers, "shape": shape, "size_nm": size}


def _pad_net(pad: Any) -> dict[str, Any]:
    """The pad's net and whether it could be read; unreadable is not unconnected."""
    try:
        name = _net_name(pad.net)
    except Exception as e:  # a net that cannot be decoded must not read as unconnected
        return {"net": None, "net_state": "unknown", "net_reason": f"{type(e).__name__}: {e}"}
    if name is None:
        return {"net": None, "net_state": "unconnected"}
    return {"net": name, "net_state": "connected"}


def _pads(self: KipyBackend, ref: BoardRef, refs: list[str] | None = None) -> dict[str, Any]:
    board = self._board(ref)
    footprints = list(board.get_footprints())
    owners = _pad_owners(footprints)
    wanted = set(refs) if refs is not None else None
    out = []
    for p in board.get_pads():
        owner = owners.get(p.id.value or "")
        if owner is None or (wanted is not None and owner[0] not in wanted):
            continue
        d: dict[str, Any] = {"ref": owner[0], "pad": owner[1], "position_nm": _vec(p.position)}
        d.update(_pad_net(p))
        d.update(_pad_geometry(p))
        out.append(d)
    _, outline = _edge_outline(board)
    return {
        "footprints": [_footprint_ref(fp) for fp in footprints],
        "pads": out,
        "outline_nm": outline,
    }


def _set_selection(self: KipyBackend, ref: BoardRef, ids: list[str]) -> None:
    from kipy.proto.common.types import KIID

    board = self._board(ref)
    board.clear_selection()
    if ids:
        items = board.get_items_by_id([KIID(value=i) for i in ids])
        if items:
            board.add_to_selection(list(items))
