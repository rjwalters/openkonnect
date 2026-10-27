"""Pieces shared by the tool bodies."""

from __future__ import annotations

import os
from typing import Any

from ..backend import NM_PER_MM, BoardRef

#: KiCad 10 has no documented "modified since last save" query (spec, "What
#: KiCad 10's IPC API allows", item 4), so dirty state is never guessed.
DIRTY_UNKNOWN_NOTE = (
    "KiCad 10's IPC API cannot report unsaved changes, so dirty is 'unknown'. "
    "Treat 'unknown' as dirty before anything that could overwrite editor work."
)


def board_document(ref: BoardRef) -> dict[str, Any]:
    return {
        "kind": "board",
        "path": ref.path,
        "path_basis": "kicad_ipc",
        "open": True,
        "dirty": "unknown",
        "dirty_basis": "none",
    }


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
    origin is the top-left corner of the Edge.Cuts bounding box, the same
    basis ``board_summary`` uses for the outline.
    """
    if outline_nm is None:
        return {
            "present": False,
            "reason": "No shapes on Edge.Cuts, so the board frame is undefined.",
        }
    return {
        "present": True,
        "x_mm": mm(outline_nm["x0_nm"]),
        "y_mm": mm(outline_nm["y0_nm"]),
        "basis": "top-left of the Edge.Cuts bounding box",
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
