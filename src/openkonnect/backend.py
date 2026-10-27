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

NM_PER_MM = 1_000_000


@dataclass
class BoardRef:
    """One board open in the PCB editor, as KiCad reports it."""

    path: str
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
        :meth:`selection`.
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

    def set_selection(self, ref: BoardRef, ids: list[str]) -> None:
        _set_selection(self, ref, ids)


def _edge_outline(board: Any) -> tuple[list[Any], dict[str, int] | None]:
    """Edge.Cuts shapes and the union of their bounding boxes (file frame, nm)."""
    from kipy.proto.board.board_types_pb2 import BoardLayer

    edge = [s for s in board.get_shapes() if s.layer == BoardLayer.BL_Edge_Cuts]
    outline = None
    if edge:
        boxes = [b for b in board.get_item_bounding_box(edge) if b is not None]
        if boxes:
            x0 = min(b.pos.x for b in boxes)
            y0 = min(b.pos.y for b in boxes)
            x1 = max(b.pos.x + b.size.x for b in boxes)
            y1 = max(b.pos.y + b.size.y for b in boxes)
            outline = {"x0_nm": x0, "y0_nm": y0, "x1_nm": x1, "y1_nm": y1}
    return edge, outline


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


def _set_selection(self: KipyBackend, ref: BoardRef, ids: list[str]) -> None:
    from kipy.proto.common.types import KIID

    board = self._board(ref)
    board.clear_selection()
    if ids:
        items = board.get_items_by_id([KIID(value=i) for i in ids])
        if items:
            board.add_to_selection(list(items))
