"""SES-08 ``get_selection``: what the user selected in the PCB editor, as file-level references.

Read live over IPC. Footprints come back as reference designators, pads as
``REF.PAD`` and copper as net names (spec C-2), so the result can be passed to
``kct`` commands as is. Items with no file-level name (tracks, vias, zones,
graphics) are listed under ``other`` with a session-scoped ID. Coordinates are
in mm in the ``file`` frame, with the offset to the ``board`` frame (C-1).
"""

from __future__ import annotations

from typing import Any

from .. import errors as E
from ..backend import SELECTION_KINDS
from ..session import Session
from .common import box_mm, frame_offset, pad_name, point_mm, require_board, union_box

DESCRIPTION = (
    "Read what is selected in KiCad's PCB editor, live, as stable file-level references "
    "that kct commands accept: footprints (reference designators), pads ('REF.PAD'), "
    "nets (of the selected copper, pads and zones), nets_of_footprints (nets reached "
    "through selected footprints' pads), other (tracks, vias, zones and graphics, with a "
    "session-scoped id and no_file_reference: true), undescribed (selected items this "
    "client cannot decode, counted, never dropped), and bbox (mm, file frame) with the "
    "board-frame offset. Optional kinds filter (e.g. ['footprint', 'pad']); "
    "include_geometry adds per-item positions and boxes. An empty selection is an "
    "ordinary empty result."
)


def parse_kinds(kinds: Any) -> list[str] | None:
    if kinds is None or kinds == []:
        return None
    if isinstance(kinds, str):
        kinds = [kinds]
    if not isinstance(kinds, (list, tuple)) or not all(isinstance(k, str) for k in kinds):
        raise E.invalid_argument(
            "kinds must be a list of item kinds.", allowed=list(SELECTION_KINDS)
        )
    bad = [k for k in kinds if k not in SELECTION_KINDS]
    if bad:
        raise E.invalid_argument(
            f"Unknown item kind(s): {', '.join(bad)}.", unknown=bad, allowed=list(SELECTION_KINDS)
        )
    return list(dict.fromkeys(kinds))


def describe_selection(data: dict[str, Any], include_geometry: bool = False) -> dict[str, Any]:
    """Turn the backend's selection data into the SES-08 result (no IPC)."""
    footprints: list[str] = []
    pads: list[str] = []
    nets: set[str] = set()
    nets_of_footprints: set[str] = set()
    other: list[dict[str, Any]] = []
    geometry: dict[str, Any] = {}
    unnamed_pads = 0

    for item in data["items"]:
        kind = item["kind"]
        if kind == "footprint":
            footprints.append(item["ref"])
            for p in item.get("pads", []):
                if p.get("net"):
                    nets_of_footprints.add(p["net"])
            if include_geometry:
                geometry[item["ref"]] = {
                    "kind": "footprint",
                    "position_mm": point_mm(item.get("position_nm")),
                    "bbox_mm": box_mm(item.get("bbox_nm")),
                    "layer": item.get("layer"),
                }
            continue
        if kind == "pad":
            name = pad_name(item)
            if item.get("net"):
                nets.add(item["net"])
            if name is not None:
                pads.append(name)
                if include_geometry:
                    geometry[name] = {
                        "kind": "pad",
                        "position_mm": point_mm(item.get("position_nm")),
                        "bbox_mm": box_mm(item.get("bbox_nm")),
                    }
                continue
            unnamed_pads += 1
        if item.get("net") and kind in ("track", "arc", "via", "zone", "shape"):
            nets.add(item["net"])
        entry: dict[str, Any] = {
            "kind": kind,
            "layer": item.get("layer"),
            "net": item.get("net"),
            "id": item.get("id"),
            "id_scope": "session",
            "no_file_reference": True,
        }
        for key in ("layers", "name"):
            if key in item:
                entry[key] = item[key]
        if include_geometry:
            entry["position_mm"] = point_mm(item.get("position_nm"))
            entry["bbox_mm"] = box_mm(item.get("bbox_nm"))
        other.append(entry)

    undescribed = data.get("undescribed") or {}
    bbox = union_box([i.get("bbox_nm") for i in data["items"]])
    result: dict[str, Any] = {
        "source": "live_editor",
        "empty": not data["items"] and not undescribed,
        "count": len(data["items"]) + sum(undescribed.values()),
        "footprints": sorted(footprints),
        "pads": sorted(pads),
        "nets": sorted(nets),
        "nets_of_footprints": sorted(nets_of_footprints),
        "other": other,
        "undescribed": {"count": sum(undescribed.values()), "by_type": dict(undescribed)},
        "bbox": box_mm(bbox),
        "bbox_basis": "union of KiCad's item bounding boxes; undescribed items not included",
        "frame_offset": frame_offset(data.get("outline_nm")),
    }
    if unnamed_pads:
        result["unnamed_pads"] = unnamed_pads
    if include_geometry:
        result["geometry"] = geometry
    return result


def get_selection(
    session: Session, kinds: Any = None, include_geometry: bool = False
) -> dict[str, Any]:
    wanted = parse_kinds(kinds)
    ref = require_board(session)
    data = session.call(lambda b: b.selection(ref, wanted))
    result = describe_selection(data, include_geometry=bool(include_geometry))
    result["board"] = {"path": ref.path}
    if wanted:
        result["kinds"] = wanted
    return result
