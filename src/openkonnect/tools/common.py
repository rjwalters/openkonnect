"""Pieces shared by the tool bodies."""

from __future__ import annotations

import os
from typing import Any

from ..backend import NM_PER_MM, BoardRef
from ..session import Session

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


# -- argument validation -------------------------------------------------------


def check_str(name: str, value: Any) -> str | None:
    """A string filter argument, or None when it is absent or empty.

    An empty string means "no filter", the same as ``None``; any non-string is
    ``invalid_argument``, reported under the argument's own name.
    """
    from .. import errors as E

    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise E.invalid_argument(f"{name} must be a string.", **{name: value})
    return value


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


def pad_name(item: dict[str, Any]) -> str | None:
    """``REF.PAD`` for a pad item, or None if its footprint is unknown (C-2)."""
    if item.get("ref") and item.get("pad") is not None:
        return f"{item['ref']}.{item['pad']}"
    return None


# -- board acquisition (SES-07) ------------------------------------------------
#
# The SES-07 fail-fast sequence (require_reachable -> open_boards ->
# no_document) is written once, in require_boards; the two helpers below are
# the only variations on it. A tool that needs an open board calls one of the
# three rather than repeating the sequence (issue #90).


def require_boards(session: Any, kind: str = "board") -> list[BoardRef]:
    """SES-07 fail-fast, then every open board, or ``no_document``.

    ``kind`` names the missing document in the error, for a caller that asked
    for something other than the board.
    """
    from .. import errors as E

    session.require_reachable()
    boards = session.call(lambda b: b.open_boards())
    if not boards:
        raise E.no_document(kind)
    return boards


def require_board(session: Any) -> BoardRef:
    """SES-07 fail-fast, then the first open board, or ``no_document``.

    The board's file is not required: an unsaved board is returned as-is. Use
    :func:`open_boards_or_fail` when the file itself is needed.
    """
    return require_boards(session)[0]


def open_boards_or_fail(session: Session, kind: str = "board") -> list[BoardRef]:
    """SES-07: fail fast on connection codes, then ``no_document``.

    A board request also needs the board's file, so a board that has never
    been saved (no path) is ``no_document`` too, with its own message.
    """
    boards = require_boards(session, kind)
    if kind == "board":
        for ref in boards:
            require_saved(ref)
    return boards
