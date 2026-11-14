"""LAY-08 ``board_outline``: the board shape and the origins the frames rest on.

Read live over IPC, so it includes unsaved editor changes; it never falls back
to the file on disk (spec C-7). The outline comes back as closed polygons in
the ``file`` frame, with the extents, the C-1 board-frame offset, and KiCad's
grid and drill origins.

A missing outline and an open (unclosed) one are reported as what they are. In
particular this tool never substitutes a rectangle for an outline it could not
close, which is what ``kicad-tools``' two file-level outline extractors do (one
falls back to a 100 x 100 mm board) and what LAY-08 forbids.
"""

from __future__ import annotations

from typing import Any

from ..session import Session
from .common import box_mm, frame_offset, mm, point_mm, require_board

DESCRIPTION = (
    "Give the outline of the board open in KiCad, live (unsaved changes included): the "
    "closed polygons that make it up (file frame, mm), the extents, the board-frame "
    "offset (C-1) and KiCad's grid and drill origins in both frames. Each loop carries "
    "its points, how many Edge.Cuts shapes it came from, and a role: 'hole' only when "
    "KiCad itself declares the ring a hole of a polygon, else 'outline' (a cutout drawn "
    "as its own shapes is another 'outline' ring; rings are never merged or subtracted, "
    "and containment is not analysed). Arcs and circles are sampled into segments, so a "
    "loop containing one is an approximation (see outline.basis). A board with no "
    "Edge.Cuts shapes is reported as "
    "present: false, and Edge.Cuts shapes that do not close are reported as "
    "closed: false with the open chains and the gap that defeated them -- never as a "
    "rectangle. Footprint-owned Edge.Cuts graphics and stroke width are excluded, as in "
    "board_summary. No board open is no_document."
)

#: Wording ``board_summary`` uses for the same condition, so the two agree.
NO_EDGE_CUTS_REASON = "No board-level shapes on Edge.Cuts."

#: KiCad reports an untouched origin as (0, 0) and gives no way to tell that
#: apart from one the user deliberately placed there.
ORIGINS_NOTE = (
    "KiCad's API reports an origin the user never moved as (0, 0) and cannot "
    "distinguish that from an origin deliberately placed at (0, 0)."
)


def _points_mm(points: list[dict[str, int]]) -> list[dict[str, float]]:
    return [{"x_mm": mm(p["x"]), "y_mm": mm(p["y"])} for p in points]


def describe_loop(loop: dict[str, Any]) -> dict[str, Any]:
    """One closed loop or open chain of the backend's outline data, in mm.

    ``role`` is ``"hole"`` only where KiCad declared the ring a hole of a
    polygon. A cutout drawn as separate Edge.Cuts shapes comes back as another
    ``"outline"`` ring: which ring encloses which is not analysed here.
    """
    entry: dict[str, Any] = {
        "closed": loop["closed"],
        "role": loop["role"],
        "frame": "file",
        "point_count": len(loop["points_nm"]),
        "source_shapes": loop["source_shapes"],
        "points_mm": _points_mm(loop["points_nm"]),
    }
    if not loop["closed"]:
        entry["gap_mm"] = mm(loop["gap_nm"])
    return entry


def _basis(data: dict[str, Any]) -> str:
    return (
        "board-level Edge.Cuts shape geometry, excluding stroke width; footprint "
        "Edge.Cuts graphics excluded. Endpoints within "
        f"{mm(data['chain_tolerance_nm'])} mm are joined; arcs and circles are sampled "
        f"every {data['arc_step_deg']} degrees, so a loop containing one is an "
        "approximation of the curve"
    )


def _origin(
    nm: dict[str, int] | None, offset_nm: dict[str, int] | None, reason: str | None
) -> dict[str, Any]:
    """One origin in both frames (C-1); ``board`` is null, never zero, with no frame."""
    if nm is None:
        return {"present": False, "reason": reason}
    board = None
    if offset_nm is not None:
        board = {
            "frame": "board",
            "x_mm": mm(nm["x"] - offset_nm["x0_nm"]),
            "y_mm": mm(nm["y"] - offset_nm["y0_nm"]),
        }
    return {"present": True, "file": point_mm(nm), "board": board}


def describe_outline(data: dict[str, Any]) -> dict[str, Any]:
    """Turn the backend's outline data into the LAY-08 result (no IPC)."""
    offset_nm = data["outline_nm"]
    loops = [describe_loop(loop) for loop in data["loops"]]
    chains = [describe_loop(chain) for chain in data["open_chains"]]

    outline: dict[str, Any]
    if data["shape_count"] == 0:
        outline = {"present": False, "reason": NO_EDGE_CUTS_REASON}
    else:
        closed = bool(loops) and not chains
        if loops and chains:
            reason = (
                f"{len(loops)} closed loop(s) found, but {len(chains)} chain(s) of "
                "Edge.Cuts shapes did not close; the smallest unclosed gap is "
                f"{min(c['gap_mm'] for c in chains)} mm."
            )
        elif loops:
            reason = None
        else:
            reason = (
                f"{data['shape_count']} Edge.Cuts shape(s) form {len(chains)} open "
                "chain(s) and no closed loop; the smallest gap between a chain's ends "
                f"is {min(c['gap_mm'] for c in chains)} mm."
                if chains
                else f"{data['shape_count']} Edge.Cuts shape(s) bound no area."
            )
        outline = {
            "present": True,
            "closed": closed,
            "reason": reason,
            "loop_count": len(loops),
            "loops": loops,
            "open_chains": chains,
            "edge_cuts_shapes": data["shape_count"],
            "basis": _basis(data),
        }

    return {
        "source": "live_editor",
        "outline": outline,
        "extents": box_mm(offset_nm),
        "frame_offset": frame_offset(offset_nm),
        "origins": {
            "grid": _origin(data["grid_origin_nm"], offset_nm, data["origins_reason"]),
            "drill": _origin(data["drill_origin_nm"], offset_nm, data["origins_reason"]),
            "note": ORIGINS_NOTE,
        },
    }


def board_outline(session: Session) -> dict[str, Any]:
    ref = require_board(session)
    data = session.call(lambda b: b.outline(ref))
    result = describe_outline(data)
    result["board"] = {"path": ref.path}
    return result
