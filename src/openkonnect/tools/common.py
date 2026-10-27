"""Pieces shared by the tool bodies."""

from __future__ import annotations

import os
from typing import Any

from ..backend import BoardRef

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
