"""LAY-03 ``footprint_pads``: the pads of footprints on the open board, read live over IPC.

``refs`` holds footprint references (``R1``: every pad) and/or ``REF.PAD`` values
(``R1.2``: that pad). Positions are mm in the ``file`` frame, with the ``board`` frame
position beside them and the offset between the two (C-1). A reference or pad that
matches nothing gives ``not_found`` (C-2); nothing is guessed. A pad whose net cannot
be read reports ``net: null`` with ``net_state: "unknown"`` and a reason, never as
unconnected.
"""

from __future__ import annotations

from typing import Any

from .. import errors as E
from ..session import Session
from .common import frame_offset, mm, require_board

DESCRIPTION = (
    "Resolve pads on the open board, live (unsaved changes included). refs is a list of "
    "footprint references ('R1': all its pads) and/or 'REF.PAD' values ('R1.2': one pad). "
    "Per pad: name ('REF.PAD'), ref, number, net (null if unconnected or unreadable, with "
    "net_state 'connected'|'unconnected'|'unknown' and net_reason when unknown), "
    "position_mm (file frame, plus position_board_mm when the board has an outline) with "
    "the frame_offset between the two, layers (canonical names), shape and size_mm. A "
    "reference or pad that matches nothing gives not_found, naming what did not match."
)


def parse_entries(refs: Any) -> list[str]:
    """The ``refs`` argument as a list of non-empty strings; a bare string is one entry."""
    if isinstance(refs, str):
        refs = [refs]
    if not isinstance(refs, (list, tuple)) or not refs:
        raise E.invalid_argument("refs must be a non-empty list of 'REF' or 'REF.PAD' values.")
    if not all(isinstance(r, str) and r.strip() for r in refs):
        raise E.invalid_argument("refs must be a non-empty list of 'REF' or 'REF.PAD' values.")
    return [r.strip() for r in refs]


def _resolve(entry: str, known: set[str]) -> tuple[str, str | None] | None:
    """Split ``entry`` into (ref, pad); a ref wins when the whole text is a known ref.

    A dot is taken as the ref/pad separator only where the left side is a known
    footprint, so a reference that contains a dot is still found whole.
    """
    if entry in known:
        return entry, None
    for i in range(len(entry) - 1, 0, -1):
        if entry[i] == "." and entry[:i] in known:
            return entry[:i], entry[i + 1 :]
    return None


def describe_pads(data: dict[str, Any], entries: list[str]) -> dict[str, Any]:
    """Turn the backend's pad data into the LAY-03 result (no IPC)."""
    offset = data.get("outline_nm")
    known = set(data["footprints"])
    by_ref: dict[str, list[dict[str, Any]]] = {}
    for p in data["pads"]:
        by_ref.setdefault(p["ref"], []).append(p)

    chosen: dict[tuple[str, str], dict[str, Any]] = {}
    not_found: list[str] = []
    for entry in entries:
        target = _resolve(entry, known)
        if target is None:
            not_found.append(entry)
            continue
        ref, pad = target
        if pad is None:
            for p in by_ref.get(ref, []):
                chosen[(ref, str(p["pad"]))] = p
            continue
        match = [p for p in by_ref.get(ref, []) if str(p["pad"]) == pad]
        if not match:
            not_found.append(entry)
            continue
        chosen[(ref, pad)] = match[0]

    pads = [_describe(p, offset) for _, p in sorted(chosen.items(), key=_sort_key)]
    return {
        "source": "live_editor",
        "count": len(pads),
        "pads": pads,
        "frame_offset": frame_offset(offset),
        "not_found": not_found,
    }


def _sort_key(item: tuple[tuple[str, str], dict[str, Any]]) -> tuple[str, int, str]:
    ref, num = item[0]
    return (ref, 0, f"{int(num):09d}") if num.isdigit() else (ref, 1, num)


def _describe(p: dict[str, Any], offset: dict[str, int] | None) -> dict[str, Any]:
    pos = p.get("position_nm")
    entry: dict[str, Any] = {
        "name": f"{p['ref']}.{p['pad']}",
        "ref": p["ref"],
        "number": p["pad"],
        "net": p.get("net"),
        "net_state": p.get("net_state", "unknown"),
    }
    if p.get("net_reason"):
        entry["net_reason"] = p["net_reason"]
    entry["position_mm"] = (
        {"frame": "file", "x_mm": mm(pos["x"]), "y_mm": mm(pos["y"])} if pos else None
    )
    if pos is not None and offset is not None:
        entry["position_board_mm"] = {
            "frame": "board",
            "x_mm": mm(pos["x"] - offset["x0_nm"]),
            "y_mm": mm(pos["y"] - offset["y0_nm"]),
        }
    entry["layers"] = list(p.get("layers", []))
    entry["shape"] = p.get("shape")
    size = p.get("size_nm")
    entry["size_mm"] = {"x_mm": mm(size["x"]), "y_mm": mm(size["y"])} if size else None
    return entry


def candidate_refs(entries: list[str]) -> list[str]:
    """Footprint refs an entry could name: the whole text and each prefix before a dot."""
    out = set(entries)
    for e in entries:
        out.update(e[:i] for i, c in enumerate(e) if c == "." and i > 0)
    return sorted(out)


def footprint_pads(session: Session, refs: Any = None) -> dict[str, Any]:
    entries = parse_entries(refs)
    board = require_board(session)
    data = session.call(lambda b: b.pads(board, candidate_refs(entries)))
    result = describe_pads(data, entries)
    if result["not_found"]:
        raise E.not_found(
            "No such footprint or pad on the open board: " + ", ".join(result["not_found"]) + ".",
            not_found=result["not_found"],
            found=[p["name"] for p in result["pads"]],
        )
    result["board"] = {"path": board.path}
    return result
