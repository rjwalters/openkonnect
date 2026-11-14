"""LAY-02 ``list_footprints``: the footprints on the open board, read live over IPC.

Reads the editor's own state, unsaved changes included. Coordinates are in mm in
the ``file`` frame, with the ``board`` frame position beside it and the offset
between the two (C-1). A field KiCad 10's IPC API does not supply is ``null``,
never guessed. A filter that matches nothing gives an empty list.
"""

from __future__ import annotations

from typing import Any

from .. import errors as E
from ..session import Session
from .common import frame_offset, mm, parse_box, point_in_box, point_mm, require_board

DESCRIPTION = (
    "List the footprints on the open board, live (unsaved changes included): refdes, "
    "value, library_id, position_mm (file frame, plus position_board_mm when the board "
    "has an outline) with the frame_offset between the two, rotation_deg, side "
    "('front' or 'back'), locked, attributes (do_not_populate, exclude_from_bom, "
    "exclude_from_position_files, not_in_schematic, mounting) and fields. Optional "
    "filters: refs (reference designators), side ('front'|'back'), box "
    "({x0_mm, y0_mm, x1_mm, y1_mm, frame: 'file'|'board'}; the frame is required; "
    "keeps footprints whose position is inside), selected_only (only what is selected "
    "in the editor). A filter matching nothing is an empty list."
)

SIDES = ("front", "back")


def side_of(layer: str | None) -> str | None:
    if layer == "F.Cu":
        return "front"
    if layer == "B.Cu":
        return "back"
    return None


def parse_refs(refs: Any) -> set[str] | None:
    if refs is None:
        return None
    if isinstance(refs, str):
        refs = [refs]
    if not isinstance(refs, (list, tuple)) or not all(isinstance(r, str) for r in refs):
        raise E.invalid_argument("refs must be a list of reference designators.")
    return set(refs)


def parse_side(side: Any) -> str | None:
    if side is None:
        return None
    if side not in SIDES:
        raise E.invalid_argument("side must be 'front' or 'back'.", allowed=list(SIDES))
    return side


def describe_footprint(fp: dict[str, Any], offset_nm: dict[str, int] | None) -> dict[str, Any]:
    """One footprint of the board index as a LAY-02 entry (no IPC)."""
    pos = point_mm(fp.get("position_nm"))
    entry: dict[str, Any] = {
        "ref": fp["ref"],
        "value": fp.get("value"),
        "library_id": fp.get("library_id"),
        "position_mm": pos,
    }
    if pos is not None and offset_nm is not None:
        entry["position_board_mm"] = {
            "frame": "board",
            "x_mm": mm(fp["position_nm"]["x"] - offset_nm["x0_nm"]),
            "y_mm": mm(fp["position_nm"]["y"] - offset_nm["y0_nm"]),
        }
    entry["rotation_deg"] = fp.get("orientation_deg")
    entry["side"] = side_of(fp.get("layer"))
    entry["layer"] = fp.get("layer")
    entry["locked"] = fp.get("locked")
    entry["attributes"] = fp.get("attributes")
    entry["fields"] = fp.get("fields")
    entry["pad_count"] = len(fp.get("pads", []))
    return entry


def _inside(fp: dict[str, Any], box: dict[str, Any], offset_nm: dict[str, int] | None) -> bool:
    return point_in_box(fp.get("position_nm"), box, offset_nm)


def list_footprints(
    session: Session,
    refs: Any = None,
    side: Any = None,
    box: Any = None,
    selected_only: bool = False,
) -> dict[str, Any]:
    wanted = parse_refs(refs)
    want_side = parse_side(side)
    area = parse_box(box)
    board = require_board(session)
    index = session.call(lambda b: b.board_index(board))
    offset = index.get("outline_nm")
    if area and area["frame"] == "board" and offset is None:
        raise E.invalid_argument(
            "box.frame 'board' needs board-level Edge.Cuts shapes, and this board has none. "
            "Use frame 'file'.",
            frame_offset=frame_offset(None),
        )
    selected_ids: set[str] | None = None
    if selected_only:
        sel = session.call(lambda b: b.selection(board, ["footprint"]))
        selected_ids = {i["id"] for i in sel["items"] if i.get("kind") == "footprint"}

    kept = []
    for fp in index["footprints"]:
        if wanted is not None and fp["ref"] not in wanted:
            continue
        if want_side and side_of(fp.get("layer")) != want_side:
            continue
        if area and not _inside(fp, area, offset):
            continue
        if selected_ids is not None and fp["id"] not in selected_ids:
            continue
        kept.append(fp)
    kept.sort(key=lambda f: f["ref"])

    result: dict[str, Any] = {
        "source": "live_editor",
        "count": len(kept),
        "footprints": [describe_footprint(f, offset) for f in kept],
        "frame_offset": frame_offset(offset),
        "filters": {
            "refs": sorted(wanted) if wanted is not None else None,
            "side": want_side,
            "box": area,
            "selected_only": bool(selected_only),
        },
        "board": {"path": board.path},
    }
    if wanted is not None:
        have = {f["ref"] for f in index["footprints"]}
        result["refs_not_on_board"] = sorted(wanted - have)
    return result
