"""SES-04 ``save_before_external_write``: make the saved file match the editor.

Run this before a file-based tool (``kct``) reads or writes a board that is
open in KiCad. See ``docs/handoff-protocol.md``.

Dirty state is the SES-01 tri-state (:func:`openkonnect.session.board_dirty_state`).
``unknown`` counts as dirty, so on KiCad 10 the board is usually saved only when
the caller passes ``allow_save``. If any requested document is refused, nothing
is saved.
"""

from __future__ import annotations

from typing import Any

from .. import errors as E
from ..backend import BoardRef
from ..errors import ErrorCode, OpenKonnectError
from ..session import (
    DirtyState,
    Session,
    bind_to_file,
    board_dirty_state,
    editor_match,
    file_state,
    read_board_text,
    read_bytes,
    sha256_bytes,
    text_sha256,
)
from .common import inferred_schematic, require_saved

DESCRIPTION = (
    "Before a file-based tool (kct) touches a board open in KiCad, make the file on disk "
    "match the editor. A board proven clean is left alone. A dirty board, or one whose "
    "dirty state is unknown (always possible on KiCad 10), is refused unless "
    "allow_save=true; then it is saved and the file is checked to have changed. Returns "
    "each document's state before, the action (none/saved/refused) and the file SHA-256 "
    "plus editor_sha256 to pass to sync_from_disk. The schematic cannot be saved over "
    "KiCad 10's API: it is refused unless schematic_unverified_ok=true."
)

_SCHEMATIC_REASON = (
    "KiCad 10's IPC API cannot inspect, save or reload the schematic (the schematic "
    "document API is KiCad 11)."
)

_KINDS = ("board", "schematic", "all")


def parse_documents(documents: Any) -> tuple[bool, bool]:
    """``(want_board, want_schematic)`` from a ``documents`` argument.

    ``None`` means every document KiCad reports open, which on KiCad 10 is the
    board only: the schematic's open state is unknown, so it is included only
    when asked for (``"schematic"`` or ``"all"``).
    """
    if documents is None:
        return True, False
    items = [documents] if isinstance(documents, str) else documents
    if not isinstance(items, (list, tuple)) or not items:
        raise E.invalid_argument(
            "documents must be 'board', 'schematic', 'all' or a list of those.",
            got=documents,
        )
    want_board = want_sch = False
    for item in items:
        if item not in _KINDS:
            raise E.invalid_argument(
                "documents must be 'board', 'schematic', 'all' or a list of those.",
                got=item,
            )
        want_board |= item in ("board", "all")
        want_sch |= item in ("schematic", "all")
    return want_board, want_sch


def open_boards_or_fail(session: Session, kind: str = "board") -> list[BoardRef]:
    """SES-07: fail fast on connection codes, then ``no_document``.

    A board request also needs the board's file, so a board that has never
    been saved (no path) is ``no_document`` too, with its own message.
    """
    session.require_reachable()
    boards = session.call(lambda b: b.open_boards())
    if not boards:
        raise E.no_document(kind)
    if kind == "board":
        for ref in boards:
            require_saved(ref)
    return boards


def schematic_unsupported(path: str | None) -> OpenKonnectError:
    return E.unsupported(
        f"{_SCHEMATIC_REASON} Save or close the schematic in KiCad yourself, then retry "
        "with schematic_unverified_ok=true, or request only the board.",
        min_kicad="11",
        kind="schematic",
        path=path,
        flag="schematic_unverified_ok",
    )


def _board_record(ref: BoardRef, ds: DirtyState) -> dict[str, Any]:
    return {"kind": "board", "path": ref.path, "state_before": ds.record()}


def _refusal(records: list[dict[str, Any]], states: list[DirtyState]) -> OpenKonnectError:
    if any(ds.dirty is True for ds in states):
        return E.document_dirty(records, flag="allow_save")
    return E.dirty_state_unknown(records, flag="allow_save")


def save_before_external_write(
    session: Session,
    documents: Any = None,
    allow_save: bool = False,
    schematic_unverified_ok: bool = False,
) -> dict[str, Any]:
    want_board, want_sch = parse_documents(documents)
    boards = open_boards_or_fail(session, "board" if want_board else "schematic")

    results: list[dict[str, Any]] = []

    if want_sch:
        sch = inferred_schematic(boards[0])
        sch_path = sch["path"] if sch else None
        if not schematic_unverified_ok:
            raise schematic_unsupported(sch_path)
        fs = file_state(sch_path) if sch_path else None
        results.append(
            {
                "kind": "schematic",
                "path": sch_path,
                "path_basis": "inferred_from_project",
                "state_before": {"dirty": "unknown", "dirty_basis": "none"},
                "action": "none",
                "caller_asserted": "schematic saved or closed (schematic_unverified_ok=true); "
                "openkonnect could not verify this on KiCad 10",
                "sha256": fs["sha256"] if fs else None,
                "exists_on_disk": bool(fs and fs["exists"]),
            }
        )

    # Phase 1: decide every board's state before acting, so a refusal saves nothing.
    plans: list[tuple[BoardRef, dict[str, Any], DirtyState]] = []
    if want_board:
        for ref in boards:
            before = file_state(ref.path)
            # A clean state counts only if it was proven on the bytes whose hash
            # this result reports as sha256 (issue #37).
            ds = bind_to_file(board_dirty_state(session, ref), before["sha256"])
            plans.append((ref, before, ds))

    protected = [(ref, ds) for ref, _, ds in plans if ds.must_protect]
    if protected and not allow_save:
        records = []
        for ref, _, ds in plans:
            rec = _board_record(ref, ds)
            rec["action"] = "refused" if ds.must_protect else "none"
            records.append(rec)
        raise _refusal(records, [ds for _, ds in protected])

    # Phase 2: save what needs saving, and check the file really changed.
    saved: list[str] = []
    for ref, before, ds in plans:
        rec = _board_record(ref, ds)
        if not ds.must_protect:
            rec.update(
                action="none",
                sha256=before["sha256"],
                file_changed=False,
                editor_sha256=ds.editor_sha256,
                editor_matches_file=True,
                match_basis=ds.match_basis,
            )
            results.append(rec)
            continue

        try:
            session.call(lambda b, ref=ref: b.save_board(ref))
        except OpenKonnectError as err:
            if err.code not in (ErrorCode.INTERNAL, ErrorCode.UNSUPPORTED_ON_THIS_KICAD):
                raise
            raise OpenKonnectError(
                ErrorCode.SAVE_FAILED,
                "KiCad refused to save the board (for example the file is read-only, or a "
                "dialog is open). See details.kicad_message, fix it in KiCad, then retry.",
                details={
                    "path": ref.path,
                    "saved_before_failure": saved,
                    "kicad_message": err.details.get("kicad_message", err.message),
                },
            ) from None

        after = file_state(ref.path)
        changed = after["exists"] and (
            after["sha256"] != before["sha256"] or after["mtime_ns"] != before["mtime_ns"]
        )
        if not changed:
            raise OpenKonnectError(
                ErrorCode.SAVE_FAILED,
                "KiCad reported the save as done, but the file on disk did not change. Do not "
                "run the file-based tool; save the board in KiCad and check its path.",
                details={
                    "path": ref.path,
                    "saved_before_failure": saved,
                    "sha256_before": before["sha256"],
                    "sha256_after": after["sha256"],
                },
            )
        saved.append(ref.path)

        # Read the editor back (C-5). Its hash is the baseline sync_from_disk
        # uses to detect edits made after this save.
        # editor_matches_file: the text, read now, equals the file, read now,
        # by bytes or (on a measured KiCad version) by the strict token
        # comparison; match_basis says which (issue #37).
        text = read_board_text(session, ref)
        basis: str | None = None
        if text is None:
            editor_sha, matches = None, "unknown"
        else:
            editor_sha = text_sha256(text)
            on_disk = read_bytes(ref.path)
            # Only the bytes this result reports as sha256 may be matched: a file
            # deleted or written again since the save check is never compared,
            # so the match is "unknown", not false (issue #50).
            if on_disk is not None and sha256_bytes(on_disk) == after["sha256"]:
                basis = editor_match(session, text, on_disk)
                matches = basis is not None
            else:
                matches = "unknown"
        rec.update(
            action="saved",
            sha256=after["sha256"],
            file_changed=True,
            editor_sha256=editor_sha,
            editor_matches_file=matches,
            match_basis=basis,
        )
        results.append(rec)

    return {
        "documents": results,
        "allow_save": allow_save,
        "schematic_unverified_ok": schematic_unverified_ok,
        "next": "Run the file-based tool, then call sync_from_disk with expected_hash (the "
        "file SHA-256 after the tool ran) and editor_hash (editor_sha256 from this result). "
        "editor_hash can only detect an edit made in KiCad since; it cannot prove the board "
        "clean. run_kct_handoff runs these steps and checks its own save itself.",
    }
