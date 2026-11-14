"""LAY-04 ``list_nets``: the nets on the open board, with net class and pad count.

Read live over IPC. With ``include_items`` each net also lists the items on it by
reference: pads as ``REF.PAD`` and copper (tracks, vias, zones, shapes) with a
session-scoped ID, the same forms ``get_selection`` emits. Items by net need
KiCad 10.0.1; below that ``include_items`` gives ``unsupported_on_this_kicad``.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any

from .. import errors as E
from ..session import Session
from .common import check_str, pad_name, require_board

MIN_KICAD_ITEMS = "10.0.1"

DESCRIPTION = (
    "List the nets on the open board, live, with each net's net class and pad count "
    "(the unnamed net is left out). Optional pattern: a case-sensitive glob on the net "
    "name (e.g. 'VIN*', '*SPI*'); optional net_class: only nets in that class (exact "
    "name). include_items adds the items on each net by reference: pads ('REF.PAD') and "
    "other copper (tracks, vias, zones, shapes, with a session-scoped id and "
    "no_file_reference: true). include_items needs KiCad 10.0.1 and gives "
    "unsupported_on_this_kicad below that. A filter that matches nothing is an empty "
    "list, not an error."
)


def version_tuple(version: str | None) -> tuple[int, ...] | None:
    """``(10, 0, 1)`` from ``'10.0.1'`` or ``'10.0.1-rc1 (x86_64)'``; None if unreadable."""
    m = re.match(r"\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", version or "")
    if not m:
        return None
    return tuple(int(p) if p else 0 for p in m.groups())


def describe_nets(
    data: dict[str, Any],
    pattern: str | None = None,
    net_class: str | None = None,
    include_items: bool = False,
) -> dict[str, Any]:
    """Turn the backend's net data into the LAY-04 result (no IPC)."""
    nets: list[dict[str, Any]] = []
    for n in data["nets"]:
        name = n["name"]
        if not name:
            continue
        if pattern is not None and not fnmatch.fnmatchcase(name, pattern):
            continue
        if net_class is not None and n.get("net_class") != net_class:
            continue
        entry: dict[str, Any] = {
            "name": name,
            "net_class": n.get("net_class"),
            "pad_count": n["pad_count"],
        }
        if include_items:
            pads: list[str] = []
            other: list[dict[str, Any]] = []
            unnamed_pads = 0
            for item in n.get("items", []):
                if item["kind"] == "pad":
                    ref = pad_name(item)
                    if ref is None:
                        unnamed_pads += 1
                    else:
                        pads.append(ref)
                else:
                    other.append(
                        {
                            "kind": item["kind"],
                            "id": item.get("id"),
                            "id_scope": "session",
                            "no_file_reference": True,
                        }
                    )
            entry["pads"] = sorted(pads)
            entry["other"] = other
            if unnamed_pads:
                entry["unnamed_pads"] = unnamed_pads
        nets.append(entry)
    nets.sort(key=lambda e: e["name"])
    result: dict[str, Any] = {"source": "live_editor", "count": len(nets), "nets": nets}
    filters = {k: v for k, v in (("pattern", pattern), ("net_class", net_class)) if v is not None}
    if filters:
        result["filters"] = filters
    return result


def list_nets(
    session: Session,
    pattern: Any = None,
    net_class: Any = None,
    include_items: bool = False,
) -> dict[str, Any]:
    pattern = check_str("pattern", pattern)
    net_class = check_str("net_class", net_class)
    items = bool(include_items)
    ref = require_board(session)
    if items:
        version = session.call(lambda b: b.kicad_version())
        have = version_tuple(version)
        need = version_tuple(MIN_KICAD_ITEMS)
        if have is None or have < need:
            raise E.unsupported(
                f"include_items needs KiCad {MIN_KICAD_ITEMS} or newer; this KiCad reports "
                f"{version or 'no version'}. Call list_nets without include_items.",
                min_kicad=MIN_KICAD_ITEMS,
                flag="include_items",
                kicad_version=version,
            )
    data = session.call(lambda b: b.nets(ref, items))
    result = describe_nets(data, pattern, net_class, items)
    result["board"] = {"path": ref.path}
    return result
