"""SES-05 ``sync_from_disk``: after an external write, make the editor show the file.

The board is reloaded with KiCad's documented "revert to the saved file"
request (kicad-python ``Board.revert()``). The reload is not an undo step.
See ``docs/handoff-protocol.md``.
"""

from __future__ import annotations

from typing import Any

from .. import errors as E
from ..backend import BoardRef
from ..errors import ErrorCode, OpenKonnectError
from ..session import (
    DirtyState,
    Session,
    board_dirty_state,
    file_state,
    read_board_text,
    text_sha256,
)
from .common import inferred_schematic
from .save_before_external_write import open_boards_or_fail, parse_documents

DESCRIPTION = (
    "After a file-based tool (kct) wrote a board that is open in KiCad, reload the editor "
    "from the file. Refuses, changing nothing, if the editor may hold unsaved work: dirty "
    "or unknown (KiCad 10 has no dirty query) unless discard_unsaved=true. To let "
    "openkonnect prove the editor is unchanged since save_before_external_write, pass "
    "editor_hash (its editor_sha256) together with editor_file_hash (its sha256, the file "
    "hash at that save) and expected_hash (the file hash after your tool wrote it; an "
    "unexpected write is refused). editor_hash without editor_file_hash proves nothing "
    "clean: the state is unknown. The reload is not an undo step. The schematic cannot be reloaded "
    "over KiCad 10's API: it is reported as manual_reload_required."
)

UNDO_NOTE = (
    "The reload is not an undo step: Ctrl+Z in KiCad will not bring back the editor's "
    "previous state. To go back, restore the earlier file and run sync_from_disk again."
)

VIEW_NOTE = (
    "KiCad 10's IPC API documents no viewport, zoom or layer-visibility query, so whether "
    "the view was kept cannot be observed."
)

SCHEMATIC_INSTRUCTIONS = (
    "KiCad 10's IPC API cannot reload the schematic. In KiCad's schematic editor, close "
    "the schematic without saving and open it again (or use File > Revert if your KiCad "
    "offers it), so it shows the file on disk."
)


def _discarded(ds: DirtyState) -> bool | str:
    return "unknown" if ds.dirty == "unknown" else bool(ds.dirty)


def sync_from_disk(
    session: Session,
    documents: Any = None,
    discard_unsaved: bool = False,
    expected_hash: str | None = None,
    editor_hash: str | None = None,
    editor_file_hash: str | None = None,
) -> dict[str, Any]:
    want_board, want_sch = parse_documents(documents)
    boards = open_boards_or_fail(session, "board" if want_board else "schematic")

    results: list[dict[str, Any]] = []
    if want_sch:
        sch = inferred_schematic(boards[0])
        results.append(
            {
                "kind": "schematic",
                "path": sch["path"] if sch else None,
                "path_basis": "inferred_from_project",
                "status": "manual_reload_required",
                "reloaded": False,
                "instructions": SCHEMATIC_INSTRUCTIONS,
                "min_kicad": "11",
            }
        )

    # Phase 1: check every board before touching any, so a refusal changes nothing.
    plans: list[tuple[BoardRef, dict[str, Any], DirtyState]] = []
    if want_board:
        for ref in boards:
            fs = file_state(ref.path)
            if not fs["exists"]:
                raise OpenKonnectError(
                    ErrorCode.NOT_FOUND,
                    "The board's file is missing on disk, so the editor was left as it is. "
                    "Restore the file, then retry.",
                    details={"path": ref.path},
                )
            if expected_hash is not None and fs["sha256"] != expected_hash:
                raise OpenKonnectError(
                    ErrorCode.STALE_INPUT,
                    "The file on disk does not have the expected hash; something else may "
                    "have written it. The editor was left as it is. Inspect the file, then "
                    "retry with its current hash.",
                    details={
                        "path": ref.path,
                        "expected_hash": expected_hash,
                        "actual_hash": fs["sha256"],
                    },
                )
            # editor_hash is a clean proof only when bound to the file: the file is
            # still the one recorded with it (editor_file_hash), or the one the
            # caller's tool wrote (expected_hash, checked just above). Issue #15.
            ds = board_dirty_state(
                session,
                ref,
                editor_baseline_sha256=editor_hash,
                baseline_file_sha256=editor_file_hash,
                written_file_sha256=expected_hash,
            )
            plans.append((ref, fs, ds))

    protected = [ds for _, _, ds in plans if ds.must_protect]
    if protected and not discard_unsaved:
        records = [
            {
                "kind": "board",
                "path": ref.path,
                "state_before": ds.record(),
                "action": "refused" if ds.must_protect else "none",
            }
            for ref, _, ds in plans
        ]
        if any(ds.dirty is True for ds in protected):
            raise E.document_dirty(records, flag="discard_unsaved")
        raise E.dirty_state_unknown(records, flag="discard_unsaved")

    # Phase 2: reload.
    for ref, fs, ds in plans:
        rec: dict[str, Any] = {"kind": "board", "path": ref.path, "state_before": ds.record()}
        before_sha = ds.editor_sha256
        try:
            session.call(lambda b, ref=ref: b.revert_board(ref))
        except OpenKonnectError as err:
            if err.code not in (ErrorCode.INTERNAL, ErrorCode.UNSUPPORTED_ON_THIS_KICAD):
                raise
            text = read_board_text(session, ref)
            if text is None or before_sha is None:
                held = "unknown"
            else:
                held = "previous" if text_sha256(text) == before_sha else "changed"
            raise OpenKonnectError(
                ErrorCode.RELOAD_FAILED,
                "KiCad rejected the file on disk (for example it does not parse), so the "
                "board was not reloaded. Fix or restore the file, then retry.",
                details={
                    "path": ref.path,
                    "editor_state": held,
                    "editor_state_note": "'previous' means the editor still holds the state "
                    "it had before this call (observed by reading it back).",
                    "kicad_message": err.details.get("kicad_message", err.message),
                },
            ) from None

        # C-5: read the editor back and compare it with the file.
        text = read_board_text(session, ref)
        if text is None:
            verified: bool | str = "unverified"
            verify_note = "KiCad did not serve the board as text, so the reload is unverified."
            after_sha = None
        else:
            after_sha = text_sha256(text)
            with open(ref.path, "rb") as f:
                same = f.read() == text.encode("utf-8")
            if same:
                verified, verify_note = True, "The editor content is byte-identical to the file."
            else:
                verified = "unverified"
                verify_note = (
                    "The editor's text differs from the file's bytes. That is expected when "
                    "the file was written by another tool (KiCad re-serializes it), so the "
                    "match could not be proven."
                )
        editor_changed: bool | str = (
            "unknown" if after_sha is None or before_sha is None else after_sha != before_sha
        )
        rec.update(
            action="reloaded",
            reloaded=True,
            discarded_unsaved=_discarded(ds),
            view_kept="unknown",
            view_note=VIEW_NOTE,
            verified=verified,
            verify_note=verify_note,
            editor_changed=editor_changed,
            sha256=fs["sha256"],
            editor_sha256=after_sha,
            undo_note=UNDO_NOTE,
        )
        if editor_changed is False:
            rec["warning"] = (
                "The editor content is the same as before the reload. Either the file holds "
                "the same board, or KiCad did not apply it; check the board in KiCad."
            )
        results.append(rec)

    return {"documents": results, "discard_unsaved": discard_unsaved, "undo_note": UNDO_NOTE}
