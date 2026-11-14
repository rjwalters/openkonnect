"""LAY-05 ``list_copper``: the tracks, arcs and vias on the open board, read live over IPC.

Reads the editor's own state, unsaved changes included. Coordinates are in mm in
the ``file`` frame, with the ``board`` frame beside them and the offset between the
two (C-1). A field KiCad 10's IPC API does not supply is ``null``, never guessed.
Item IDs are session-scoped and have no file reference. A filter that matches
nothing gives an empty list.

Zones are **not** here: they carry copper, but they are LAY-06 ``list_zones``.
"""

from __future__ import annotations

from typing import Any

from .. import errors as E
from ..session import Session
from .common import (
    board_point_mm,
    frame_offset,
    is_layer,
    mm,
    parse_box,
    point_in_box,
    point_mm,
    require_board,
)

DESCRIPTION = (
    "List the tracks, arcs and vias on the open board, live, unsaved changes included "
    "(zones are list_zones). Per item: a session-scoped id (no_file_reference: true), "
    "kind ('track', 'arc' or 'via'), net (null if unconnected), locked, and the "
    "endpoints or centre in both C-1 frames (the _board_mm points only when the board "
    "has an outline). A track or arc also has layer (canonical name), width_mm and "
    "length_mm (an arc's true arc length, not its chord); an arc has mid_mm. A via "
    "instead has layers (its drill span), position_mm, diameter_mm, drill_mm and "
    "via_type, and contributes no length. total_length_mm_by_net totals the "
    "**filtered** items per named net. Optional filters: net (exact name), layer "
    "(canonical name; a via matches on either end of its span), box "
    "({x0_mm, y0_mm, x1_mm, y1_mm, frame: 'file'|'board'}; the frame is required; "
    "keeps an item when any point it reports is inside), locked_only. A filter "
    "matching nothing is an empty list; an unknown layer is invalid_argument."
)


def _check_str(name: str, value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise E.invalid_argument(f"{name} must be a string.", **{name: value})
    return value


def points_nm(item: dict[str, Any]) -> list[dict[str, int]]:
    """Every point the item reports, in the file frame: what the ``box`` filter tests."""
    keys = ("start_nm", "mid_nm", "end_nm", "position_nm")
    return [p for p in (item.get(k) for k in keys) if p is not None]


def describe_item(item: dict[str, Any], offset_nm: dict[str, int] | None) -> dict[str, Any]:
    """One backend copper item as a LAY-05 entry (no IPC)."""
    entry: dict[str, Any] = {
        "id": item.get("id"),
        "id_scope": "session",
        "no_file_reference": True,
        "kind": item["kind"],
        "net": item.get("net"),
        "layer": item.get("layer"),
        "layers": item.get("layers"),
    }
    if item["kind"] == "via":
        entry["width_mm"] = None
        entry["position_mm"] = point_mm(item.get("position_nm"))
        entry["position_board_mm"] = board_point_mm(item.get("position_nm"), offset_nm)
        entry["diameter_mm"] = _opt_mm(item.get("diameter_nm"))
        entry["drill_mm"] = _opt_mm(item.get("drill_nm"))
        entry["via_type"] = item.get("via_type")
        #: A via spans layers rather than running along one: it has no length (C-2).
        entry["length_mm"] = None
    else:
        entry["width_mm"] = _opt_mm(item.get("width_nm"))
        for name in ("start", "mid", "end"):
            point = item.get(f"{name}_nm")
            if name == "mid" and point is None:
                continue
            entry[f"{name}_mm"] = point_mm(point)
            entry[f"{name}_board_mm"] = board_point_mm(point, offset_nm)
        entry["diameter_mm"] = None
        entry["drill_mm"] = None
        entry["via_type"] = None
        entry["length_mm"] = _opt_mm(item.get("length_nm"))
    entry["locked"] = item.get("locked")
    return entry


def _opt_mm(nm: float | None) -> float | None:
    return None if nm is None else mm(nm)


def _on_layer(item: dict[str, Any], layer: str) -> bool:
    """A track or arc matches its own layer; a via matches either end of its span."""
    return item.get("layer") == layer or layer in (item.get("layers") or [])


def describe_copper(
    data: dict[str, Any],
    net: str | None = None,
    layer: str | None = None,
    box: dict[str, Any] | None = None,
    locked_only: bool = False,
) -> dict[str, Any]:
    """Turn the backend's copper data into the LAY-05 result (no IPC)."""
    offset = data.get("outline_nm")
    kept: list[dict[str, Any]] = []
    for item in data["copper"]:
        if net is not None and item.get("net") != net:
            continue
        if layer is not None and not _on_layer(item, layer):
            continue
        if box is not None and not any(point_in_box(p, box, offset) for p in points_nm(item)):
            continue
        if locked_only and not item.get("locked"):
            continue
        kept.append(item)
    kept.sort(key=lambda e: (e["kind"], e.get("net") or "", e.get("id") or ""))

    # Totals cover the FILTERED items, not the whole board: filtering is the point of
    # the tool, and `filters` below says what the number is a total of. A named net
    # that kept only vias totals 0.0 rather than vanishing.
    totals_nm: dict[str, float] = {}
    for item in kept:
        name = item.get("net")
        if name:
            totals_nm[name] = totals_nm.get(name, 0.0) + float(item.get("length_nm") or 0.0)

    return {
        "source": "live_editor",
        "count": len(kept),
        "copper": [describe_item(i, offset) for i in kept],
        "total_length_mm_by_net": {name: mm(totals_nm[name]) for name in sorted(totals_nm)},
        "frame_offset": frame_offset(offset),
        "filters": {
            "net": net,
            "layer": layer,
            "box": box,
            "locked_only": bool(locked_only),
        },
    }


def list_copper(
    session: Session,
    net: Any = None,
    layer: Any = None,
    box: Any = None,
    locked_only: bool = False,
) -> dict[str, Any]:
    net = _check_str("net", net)
    layer = _check_str("layer", layer)
    if layer is not None and not is_layer(layer):
        raise E.invalid_argument(
            f"Unknown layer {layer!r}. Use a canonical name such as 'F.Cu', 'In1.Cu' or 'B.Cu'.",
            layer=layer,
        )
    area = parse_box(box)
    if not isinstance(locked_only, bool):
        raise E.invalid_argument("locked_only must be true or false.", locked_only=locked_only)
    ref = require_board(session)
    data = session.call(lambda b: b.copper(ref))
    if area is not None and area["frame"] == "board" and data.get("outline_nm") is None:
        raise E.invalid_argument(
            "box.frame 'board' needs board-level Edge.Cuts shapes, and this board has none. "
            "Use frame 'file'.",
            frame_offset=frame_offset(None),
        )
    result = describe_copper(data, net, layer, area, locked_only)
    result["board"] = {"path": ref.path}
    return result
