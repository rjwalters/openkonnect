"""SES-01 ``session_status``: is KiCad reachable, and what does it have open.

"Not running", "API disabled" and "unreachable" are returned as data with
``ok: true``. Only ``kicad_busy``, ``timeout`` and ``instance_changed`` are
errors.
"""

from __future__ import annotations

from typing import Any

from ..backend import client_api_version
from ..session import Session
from .common import DIRTY_UNKNOWN_NOTE, board_document, inferred_schematic
from .run_kct_handoff import find_kct

DESCRIPTION = (
    "Report whether KiCad is running and its IPC API reachable, the KiCad and API "
    "versions, the instance, the project directory, the open documents with their "
    "dirty state, and which tools this instance supports. Always answers: "
    "'not running' and 'API disabled' come back as data."
)

_STATIC_CAPABILITIES = {
    "session_status": {"supported": True},
    "dirty_state_query": {
        "supported": False,
        "reason": "KiCad 10's IPC API has no modified-since-save query (KiCad 11). "
        "session_status reports dirty 'unknown'; save_before_external_write and "
        "sync_from_disk compare the editor's native-format text instead "
        "(dirty_basis 'content_comparison') where that proves the state.",
        "min_kicad": "11",
    },
    "schematic_save_reload": {
        "supported": False,
        "reason": "KiCad 10's IPC API cannot save or reload the schematic (KiCad 11). "
        "save_before_external_write refuses it unless schematic_unverified_ok; "
        "sync_from_disk reports manual_reload_required.",
        "min_kicad": "11",
    },
    "live_schematic": {
        "supported": False,
        "reason": "KiCad 10's IPC API serves the PCB editor only (schematic API is KiCad 11).",
        "min_kicad": "11",
    },
}


#: Tools that need a reachable KiCad with a board open.
_BOARD_TOOLS = (
    "board_summary",
    "save_before_external_write",
    "sync_from_disk",
    "run_kct_handoff",
)


def session_status(session: Session) -> dict[str, Any]:
    probe = session.probe()
    result: dict[str, Any] = {
        "kicad_running": probe.kicad_running,
        "ipc_reachable": probe.ipc_reachable,
        "state": probe.state,
        "kicad_version": None,
        "api_version": client_api_version(),
        "instance": None,
        "instances_found": probe.sockets_found,
        "project_dir": None,
        "documents": [],
    }
    if probe.api_enabled_in_config is not None:
        result["api_enabled_in_config"] = probe.api_enabled_in_config

    capabilities = {k: dict(v) for k, v in _STATIC_CAPABILITIES.items()}

    if not probe.ipc_reachable:
        assert probe.reason is not None
        result["reason"] = probe.reason.to_dict()
        for tool in _BOARD_TOOLS:
            capabilities[tool] = {
                "supported": False,
                "reason": probe.reason.message,
                "error_code": probe.reason.code.value,
            }
        result["capabilities"] = capabilities
        return result

    result["kicad_version"] = session.call(lambda b: b.kicad_version())
    result["instance"] = session.identity
    boards = session.call(lambda b: b.open_boards())

    docs: list[dict[str, Any]] = []
    for ref in boards:
        docs.append(board_document(ref))
    if boards:
        result["project_dir"] = boards[0].project_dir
        sch = inferred_schematic(boards[0])
        if sch is not None:
            docs.append(sch)
        result["dirty_note"] = DIRTY_UNKNOWN_NOTE
    result["documents"] = docs

    for tool in _BOARD_TOOLS:
        capabilities[tool] = (
            {"supported": True}
            if boards
            else {"supported": False, "reason": "No board is open.", "error_code": "no_document"}
        )
    if boards and find_kct() is None:
        capabilities["run_kct_handoff"] = {
            "supported": False,
            "reason": "The kct executable (kicad-tools) was not found on PATH.",
            "error_code": "kicad_tools_failed",
        }
    result["capabilities"] = capabilities
    return result
