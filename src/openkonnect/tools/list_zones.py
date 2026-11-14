"""LAY-06 ``list_zones``: the copper zones and rule areas on the open board.

Read live over IPC. A rule area (keepout) reports what it keeps out; a copper zone
reports its net and pad-connection style. Zone IDs are session-scoped and have no
file reference.
"""

from __future__ import annotations

from typing import Any

from .. import errors as E
from ..session import Session
from .common import is_layer, require_board

DESCRIPTION = (
    "List the copper zones and rule areas (keepouts) on the open board, live, unsaved "
    "changes included. Per zone: a session-scoped id (no_file_reference: true), kind "
    "('copper' or 'rule_area'), name (null if unnamed), net, layers (canonical names), "
    "priority, pad_connection (inherited, none, thermal, full, pth_thermal; null for a "
    "rule area), filled, locked, and for a rule area keepouts (copper, vias, tracks, pads, "
    "footprints booleans). Optional net: exact net name; optional layer: canonical layer "
    "name (e.g. 'F.Cu'), keeps zones on that layer. A filter that matches nothing is an "
    "empty list; an unknown layer is invalid_argument."
)


def _check_str(name: str, value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise E.invalid_argument(f"{name} must be a string.", **{name: value})
    return value


def describe_zones(
    data: dict[str, Any], net: str | None = None, layer: str | None = None
) -> dict[str, Any]:
    """Turn the backend's zone data into the LAY-06 result (no IPC)."""
    zones: list[dict[str, Any]] = []
    for z in data["zones"]:
        if net is not None and z.get("net") != net:
            continue
        if layer is not None and layer not in z.get("layers", []):
            continue
        zones.append(
            {
                "id": z.get("id"),
                "id_scope": "session",
                "no_file_reference": True,
                "kind": z["kind"],
                "name": z.get("name"),
                "net": z.get("net"),
                "layers": list(z.get("layers", [])),
                "priority": z.get("priority"),
                "pad_connection": z.get("pad_connection"),
                "filled": z.get("filled"),
                "locked": z.get("locked"),
                "keepouts": z.get("keepouts"),
            }
        )
    zones.sort(key=lambda e: (e["name"] or "", e["id"] or ""))
    result: dict[str, Any] = {"source": "live_editor", "count": len(zones), "zones": zones}
    filters = {k: v for k, v in (("net", net), ("layer", layer)) if v is not None}
    if filters:
        result["filters"] = filters
    return result


def list_zones(session: Session, net: Any = None, layer: Any = None) -> dict[str, Any]:
    net = _check_str("net", net)
    layer = _check_str("layer", layer)
    if layer is not None and not is_layer(layer):
        raise E.invalid_argument(
            f"Unknown layer {layer!r}. Use a canonical name such as 'F.Cu', 'In1.Cu' or 'B.Cu'.",
            layer=layer,
        )
    ref = require_board(session)
    data = session.call(lambda b: b.zones(ref))
    result = describe_zones(data, net, layer)
    result["board"] = {"path": ref.path}
    return result
