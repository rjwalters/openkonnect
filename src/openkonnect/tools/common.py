"""Pieces shared by the tool bodies."""

from __future__ import annotations

import os
import re
from typing import Any

from ..backend import NM_PER_MM, BoardRef

#: KiCad 10 has no documented "modified since last save" query (spec, "What
#: KiCad 10's IPC API allows", item 4), so dirty state is never guessed.
DIRTY_UNKNOWN_NOTE = (
    "KiCad 10's IPC API cannot report unsaved changes, so dirty is 'unknown'. "
    "Treat 'unknown' as dirty before anything that could overwrite editor work."
)


#: Why a board with no file is refused by tools that need the file.
UNSAVED_BOARD_REASON = (
    "The open board has never been saved, so it has no file. Save it in KiCad "
    "(File > Save As), then call this tool again."
)


def board_document(ref: BoardRef) -> dict[str, Any]:
    if ref.path is None:
        return {
            "kind": "board",
            "path": None,
            "path_basis": "unsaved",
            "path_note": "KiCad reports no file name and no project: the board is new "
            "and has never been saved.",
            "open": True,
            "dirty": "unknown",
            "dirty_basis": "none",
        }
    return {
        "kind": "board",
        "path": ref.path,
        "path_basis": "kicad_ipc",
        "open": True,
        "dirty": "unknown",
        "dirty_basis": "none",
    }


def require_saved(ref: BoardRef) -> str:
    """The board's file path, or ``no_document`` if it has never been saved."""
    from ..errors import ErrorCode, OpenKonnectError

    if ref.path is None:
        raise OpenKonnectError(
            ErrorCode.NO_DOCUMENT,
            UNSAVED_BOARD_REASON,
            details={"kind": "board", "saved": False},
        )
    return ref.path


def inferred_schematic(ref: BoardRef) -> dict[str, Any] | None:
    """The project's root schematic, inferred from the project (not served on KiCad 10)."""
    if not ref.project_dir or not ref.project_name:
        return None
    path = os.path.join(ref.project_dir, f"{ref.project_name}.kicad_sch")
    return {
        "kind": "schematic",
        "path": path,
        "path_basis": "inferred_from_project",
        "exists_on_disk": os.path.isfile(path),
        "open": "unknown",
        "dirty": "unknown",
        "dirty_basis": "none",
    }


# -- coordinates (spec C-1) ----------------------------------------------------


def mm(nm: float) -> float:
    """Nanometres to millimetres, rounded to 1 nm."""
    return round(nm / NM_PER_MM, 6)


def frame_offset(outline_nm: dict[str, int] | None) -> dict[str, Any]:
    """The ``board`` frame's origin in the ``file`` frame (C-1).

    ``board`` coordinates are ``file`` coordinates minus this offset. The
    origin is the top-left corner of the board-level Edge.Cuts shapes'
    geometry, without their stroke width, the same basis ``board_summary`` uses
    for the outline. Footprint-owned Edge.Cuts graphics do not count, as in
    ``kct``'s board origin.
    """
    if outline_nm is None:
        return {
            "present": False,
            "reason": "No board-level shapes on Edge.Cuts, so the board frame is undefined.",
        }
    return {
        "present": True,
        "x_mm": mm(outline_nm["x0_nm"]),
        "y_mm": mm(outline_nm["y0_nm"]),
        "basis": (
            "top-left of the board-level Edge.Cuts geometry bounding box, excluding"
            " stroke width; footprint Edge.Cuts graphics excluded"
        ),
        "board_from_file": "board = file - offset",
    }


def union_box(boxes: list[dict[str, int] | None]) -> dict[str, int] | None:
    real = [b for b in boxes if b]
    if not real:
        return None
    return {
        "x0_nm": min(b["x0_nm"] for b in real),
        "y0_nm": min(b["y0_nm"] for b in real),
        "x1_nm": max(b["x1_nm"] for b in real),
        "y1_nm": max(b["y1_nm"] for b in real),
    }


def box_mm(box: dict[str, int] | None) -> dict[str, Any] | None:
    if box is None:
        return None
    return {
        "frame": "file",
        "x0_mm": mm(box["x0_nm"]),
        "y0_mm": mm(box["y0_nm"]),
        "x1_mm": mm(box["x1_nm"]),
        "y1_mm": mm(box["y1_nm"]),
        "width_mm": mm(box["x1_nm"] - box["x0_nm"]),
        "height_mm": mm(box["y1_nm"] - box["y0_nm"]),
    }


def point_mm(p: dict[str, int] | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {"frame": "file", "x_mm": mm(p["x"]), "y_mm": mm(p["y"])}


# -- canonical layer names (spec C-1 layer filters) ----------------------------

#: KiCad 10's fixed (non-numbered) layer names.
FIXED_LAYERS = frozenset(
    {
        "F.Cu",
        "B.Cu",
        "F.Adhes",
        "B.Adhes",
        "F.Paste",
        "B.Paste",
        "F.SilkS",
        "B.SilkS",
        "F.Mask",
        "B.Mask",
        "Dwgs.User",
        "Cmts.User",
        "Eco1.User",
        "Eco2.User",
        "Edge.Cuts",
        "Margin",
        "F.CrtYd",
        "B.CrtYd",
        "F.Fab",
        "B.Fab",
    }
)
_INNER_CU = re.compile(r"In([1-9]|[12]\d|30)\.Cu")
_USER_LAYER = re.compile(r"User\.([1-9]|[1-9]\d|1\d\d)")


def is_layer(name: str) -> bool:
    """True for a canonical KiCad layer name: fixed, ``In1..In30.Cu`` or ``User.1..User.199``."""
    return name in FIXED_LAYERS or bool(_INNER_CU.fullmatch(name) or _USER_LAYER.fullmatch(name))


# -- the box filter (spec C-1) -------------------------------------------------

#: The two coordinate frames a ``box`` filter may name. C-1 gives neither a default.
FRAMES = ("file", "board")


def parse_box(box: Any) -> dict[str, Any] | None:
    """Validate the ``box`` filter; returns mm corners, ordered, and the frame."""
    from .. import errors as E

    if box is None:
        return None
    keys = ("x0_mm", "y0_mm", "x1_mm", "y1_mm")
    if not isinstance(box, dict):
        raise E.invalid_argument(
            "box must be an object with x0_mm, y0_mm, x1_mm, y1_mm and frame.", keys=list(keys)
        )
    frame = box.get("frame")
    if frame not in FRAMES:
        raise E.invalid_argument(
            "box.frame is required: 'file' or 'board' (spec C-1 has no default frame).",
            allowed=list(FRAMES),
        )
    vals: list[float] = []
    for k in keys:
        v = box.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise E.invalid_argument(f"box.{k} must be a number (mm).", keys=list(keys))
        vals.append(float(v))
    x0, y0, x1, y1 = vals
    return {
        "frame": frame,
        "x0_mm": min(x0, x1),
        "y0_mm": min(y0, y1),
        "x1_mm": max(x0, x1),
        "y1_mm": max(y0, y1),
    }


def point_in_box(
    point_nm: dict[str, int] | None,
    box: dict[str, Any],
    offset_nm: dict[str, int] | None,
) -> bool:
    """Is ``point_nm`` (file frame) inside ``box``, which may be in either frame?"""
    if point_nm is None:
        return False
    x, y = mm(point_nm["x"]), mm(point_nm["y"])
    if box["frame"] == "board":
        x -= mm(offset_nm["x0_nm"]) if offset_nm else 0
        y -= mm(offset_nm["y0_nm"]) if offset_nm else 0
    return box["x0_mm"] <= x <= box["x1_mm"] and box["y0_mm"] <= y <= box["y1_mm"]


def board_point_mm(
    point_nm: dict[str, int] | None, offset_nm: dict[str, int] | None
) -> dict[str, Any] | None:
    """``point_nm`` in the ``board`` frame, or None when that frame is undefined (C-1)."""
    if point_nm is None or offset_nm is None:
        return None
    return {
        "frame": "board",
        "x_mm": mm(point_nm["x"] - offset_nm["x0_nm"]),
        "y_mm": mm(point_nm["y"] - offset_nm["y0_nm"]),
    }


def pad_name(item: dict[str, Any]) -> str | None:
    """``REF.PAD`` for a pad item, or None if its footprint is unknown (C-2)."""
    if item.get("ref") and item.get("pad") is not None:
        return f"{item['ref']}.{item['pad']}"
    return None


def require_board(session: Any) -> BoardRef:
    """SES-07 fail-fast, then the first open board, or ``no_document``."""
    from .. import errors as E

    session.require_reachable()
    boards = session.call(lambda b: b.open_boards())
    if not boards:
        raise E.no_document("board")
    return boards[0]
