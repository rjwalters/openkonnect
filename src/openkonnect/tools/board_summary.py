"""LAY-01 ``board_summary``: a one-call overview of the board open in KiCad.

Read live over IPC, so it includes unsaved editor changes. It never falls back
to the file on disk (spec C-7).
"""

from __future__ import annotations

from typing import Any

from .. import errors as E
from ..backend import NM_PER_MM
from ..session import Session
from .common import DIRTY_UNKNOWN_NOTE, board_document

DESCRIPTION = (
    "Summarize the board open in KiCad's PCB editor, live (including unsaved changes): "
    "path, title block, copper and total layer counts, counts of footprints, nets, "
    "tracks, vias and zones, outline size, and dirty state. Fails fast with a "
    "structured error when KiCad is unreachable or no board is open."
)


def _mm(nm: int) -> float:
    return round(nm / NM_PER_MM, 4)


def board_summary(session: Session) -> dict[str, Any]:
    session.require_reachable()
    boards = session.call(lambda b: b.open_boards())
    if not boards:
        raise E.no_document("board")
    ref = boards[0]
    data = session.call(lambda b: b.board_data(ref))

    box = data["outline_nm"]
    if box is None:
        outline: dict[str, Any] = {
            "present": False,
            "reason": "No board-level shapes on Edge.Cuts.",
        }
    else:
        outline = {
            "present": True,
            "width_mm": _mm(box["x1_nm"] - box["x0_nm"]),
            "height_mm": _mm(box["y1_nm"] - box["y0_nm"]),
            "frame": "file",
            "origin_mm": {"x": _mm(box["x0_nm"]), "y": _mm(box["y0_nm"])},
            "basis": (
                "bounding box of board-level Edge.Cuts shape geometry, excluding"
                " stroke width; footprint Edge.Cuts graphics excluded"
            ),
        }

    doc = board_document(ref)
    return {
        "source": "live_editor",
        "board": {"path": ref.path, "project_dir": ref.project_dir},
        "title_block": data["title_block"],
        "layers": {"copper": data["copper_layers"], "total": data["enabled_layers"]},
        "counts": {
            "footprints": data["footprints"],
            "nets": data["nets"],
            "tracks": data["track_segments"] + data["track_arcs"],
            "track_segments": data["track_segments"],
            "track_arcs": data["track_arcs"],
            "vias": data["vias"],
            "zones": data["zones"],
            "rule_areas": data["rule_areas"],
        },
        "outline": outline,
        "dirty": doc["dirty"],
        "dirty_basis": doc["dirty_basis"],
        "dirty_note": DIRTY_UNKNOWN_NOTE,
        "boards_open": len(boards),
    }
