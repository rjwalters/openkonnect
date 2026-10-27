"""SES-05 ``sync_from_disk`` against a fake IPC backend."""

from __future__ import annotations

from pathlib import Path

import pytest
from kipy.errors import ApiError
from kipy.proto.common import ApiStatusCode

from openkonnect.session import board_dirty_state
from openkonnect.tools.sync_from_disk import sync_from_disk

from .conftest import make_session
from .test_save_before_external_write import (
    CODES,
    EDITED_TEXT,
    FILE_TEXT,
    SyncFake,
    board_on_disk,
    connection_cases,
    sha,
)

KCT_TEXT = '(kicad_pcb (version 20241229) (generator "kct") (routed yes))\n'


def sync(session, **kw):
    return session.run_tool(lambda s: sync_from_disk(s, **kw))


def after_external_write(tmp_path, editor=FILE_TEXT, **kw):
    """The editor holds ``editor``; an external tool has written KCT_TEXT to the file."""
    ref = board_on_disk(tmp_path, KCT_TEXT)
    return ref, SyncFake(ref, editor, **kw)


def test_unknown_without_discard_refuses_and_changes_nothing(tmp_path):
    ref, fake = after_external_write(tmp_path)
    r = sync(make_session(fake))
    assert r["ok"] is False
    assert r["error"]["code"] == "dirty_state_unknown"
    assert r["error"]["details"]["flag"] == "discard_unsaved"
    assert "revert_board" not in fake.calls
    assert fake.editor == FILE_TEXT


def test_baseline_unchanged_reloads_without_discard(tmp_path):
    ref, fake = after_external_write(tmp_path)
    r = sync(
        make_session(fake),
        editor_hash=sha(FILE_TEXT),
        editor_file_hash=sha(FILE_TEXT),
        expected_hash=sha(KCT_TEXT),
    )
    assert r["ok"] is True, r
    (doc,) = r["documents"]
    assert doc["state_before"]["dirty"] is False
    assert doc["state_before"]["dirty_basis"] == "content_comparison"
    assert doc["action"] == "reloaded"
    assert doc["reloaded"] is True
    assert doc["discarded_unsaved"] is False
    assert doc["view_kept"] == "unknown"
    assert doc["verified"] is True
    assert doc["editor_changed"] is True
    assert "not an undo step" in doc["undo_note"]
    assert fake.editor == KCT_TEXT


def test_user_edit_since_baseline_is_document_dirty(tmp_path):
    ref, fake = after_external_write(tmp_path, EDITED_TEXT)
    r = sync(make_session(fake), editor_hash=sha(FILE_TEXT))
    assert r["error"]["code"] == "document_dirty"
    assert r["error"]["details"]["documents"][0]["state_before"]["dirty"] is True
    assert "revert_board" not in fake.calls
    assert fake.editor == EDITED_TEXT


@pytest.mark.parametrize(("editor_hash", "discarded"), [(sha(FILE_TEXT), True), (None, "unknown")])
def test_discard_unsaved_reloads(tmp_path, editor_hash, discarded):
    ref, fake = after_external_write(tmp_path, EDITED_TEXT)
    r = sync(make_session(fake), discard_unsaved=True, editor_hash=editor_hash)
    assert r["ok"] is True, r
    assert r["documents"][0]["discarded_unsaved"] == discarded
    assert fake.editor == KCT_TEXT


def test_missing_file_refuses(tmp_path):
    ref, fake = after_external_write(tmp_path)
    Path(ref.path).unlink()
    r = sync(make_session(fake), discard_unsaved=True)
    assert r["error"]["code"] == "not_found"
    assert "revert_board" not in fake.calls


def test_expected_hash_mismatch_refuses(tmp_path):
    ref, fake = after_external_write(tmp_path)
    r = sync(make_session(fake), discard_unsaved=True, expected_hash=sha("something else"))
    assert r["error"]["code"] == "stale_input"
    assert r["error"]["details"]["actual_hash"] == sha(KCT_TEXT)
    assert "revert_board" not in fake.calls
    assert fake.editor == FILE_TEXT


def test_file_kicad_rejects_reports_previous_state(tmp_path):
    err = ApiError("could not parse board", code=ApiStatusCode.AS_BAD_REQUEST)
    ref, fake = after_external_write(tmp_path, revert_error=err)
    r = sync(make_session(fake), editor_hash=sha(FILE_TEXT), editor_file_hash=sha(KCT_TEXT))
    assert r["ok"] is False
    assert r["error"]["code"] == "reload_failed"
    assert r["error"]["details"]["editor_state"] == "previous"
    assert "parse" in r["error"]["details"]["kicad_message"]


def test_reformatted_file_is_unverified_not_failed(tmp_path):
    ref, fake = after_external_write(tmp_path)

    def revert(_ref):
        fake.calls.append("revert_board")
        fake.editor = KCT_TEXT.replace(" ", "  ")  # KiCad re-serializes differently

    fake.revert_board = revert
    r = sync(
        make_session(fake),
        editor_hash=sha(FILE_TEXT),
        editor_file_hash=sha(FILE_TEXT),
        expected_hash=sha(KCT_TEXT),
    )
    assert r["ok"] is True, r
    assert r["documents"][0]["verified"] == "unverified"


def test_text_not_served_needs_discard_and_is_unverified(tmp_path):
    ref, fake = after_external_write(tmp_path, text_supported=False)
    r = sync(make_session(fake), editor_hash=sha(FILE_TEXT))
    assert r["error"]["code"] == "dirty_state_unknown"
    r = sync(make_session(fake), discard_unsaved=True)
    assert r["ok"] is True, r
    doc = r["documents"][0]
    assert doc["verified"] == "unverified"
    assert doc["editor_changed"] == "unknown"


def test_schematic_is_manual_reload_required(tmp_path):
    ref, fake = after_external_write(tmp_path)
    r = sync(make_session(fake), documents="schematic")
    assert r["ok"] is True, r
    (doc,) = r["documents"]
    assert doc["kind"] == "schematic"
    assert doc["status"] == "manual_reload_required"
    assert doc["reloaded"] is False
    assert "schematic editor" in doc["instructions"]
    assert "revert_board" not in fake.calls


@pytest.mark.parametrize("code", CODES)
def test_connection_errors_fail_fast(tmp_path, code):
    ref = board_on_disk(tmp_path)
    _, session_kw, script = next(c for c in connection_cases(ref) if c[0] == code)
    fake = SyncFake(ref, EDITED_TEXT, **script)
    r = sync(make_session(fake, **session_kw), discard_unsaved=True)
    assert r["error"]["code"] == code
    assert "revert_board" not in fake.calls


# Issue #15: a recorded editor_hash alone must never prove the board clean.


def save_then_undo(tmp_path):
    """Baseline H0 = FILE_TEXT (file hash F0). The human then saved an edit in KiCad
    (file now EDITED_TEXT) and undid back, so the editor again holds H0 while the
    file differs: the editor's state is unsaved relative to the last save."""
    ref = board_on_disk(tmp_path, EDITED_TEXT)
    return ref, SyncFake(ref, FILE_TEXT)


@pytest.mark.parametrize("binding", [None, sha(FILE_TEXT)], ids=["unbound", "stale_bound"])
def test_save_then_undo_is_unknown_and_not_reloaded(tmp_path, binding):
    ref, fake = save_then_undo(tmp_path)
    r = sync(make_session(fake), editor_hash=sha(FILE_TEXT), editor_file_hash=binding)
    assert r["ok"] is False, r
    assert r["error"]["code"] == "dirty_state_unknown"
    assert r["error"]["details"]["documents"][0]["state_before"]["dirty"] == "unknown"
    assert "revert_board" not in fake.calls
    assert fake.editor == FILE_TEXT


@pytest.mark.parametrize("binding", [None, sha(FILE_TEXT)], ids=["unbound", "stale_bound"])
def test_save_then_undo_with_discard_reports_unknown(tmp_path, binding):
    ref, fake = save_then_undo(tmp_path)
    r = sync(
        make_session(fake),
        editor_hash=sha(FILE_TEXT),
        editor_file_hash=binding,
        discard_unsaved=True,
    )
    assert r["ok"] is True, r
    assert r["documents"][0]["discarded_unsaved"] == "unknown"
    assert fake.editor == EDITED_TEXT


def test_editor_hash_alone_after_external_write_is_unknown(tmp_path):
    ref, fake = after_external_write(tmp_path)
    r = sync(make_session(fake), editor_hash=sha(FILE_TEXT), expected_hash=sha(KCT_TEXT))
    assert r["error"]["code"] == "dirty_state_unknown"
    assert "revert_board" not in fake.calls


def test_binding_without_expected_hash_after_write_is_unknown(tmp_path):
    # The file changed since the baseline, and the caller does not vouch for the new
    # content with expected_hash: the change may be a save from KiCad.
    ref, fake = after_external_write(tmp_path)
    r = sync(make_session(fake), editor_hash=sha(FILE_TEXT), editor_file_hash=sha(FILE_TEXT))
    assert r["error"]["code"] == "dirty_state_unknown"
    assert "revert_board" not in fake.calls


# board_dirty_state over baseline x binding: False only for match + bound + fresh.

F_BASE, F_KCT, F_OTHER = sha(FILE_TEXT), sha(KCT_TEXT), sha("something else")


def dirty_state(fake, ref, **kw):
    def body(s):
        s.require_reachable()
        return {"ds": board_dirty_state(s, ref, **kw)}

    r = make_session(fake).run_tool(body)
    assert r["ok"] is True, r
    return r["ds"]


@pytest.mark.parametrize(
    ("baseline", "bound", "written", "want"),
    [
        (sha(FILE_TEXT), None, None, "unknown"),  # match, unbound
        (sha(FILE_TEXT), None, F_KCT, "unknown"),  # match, written hash alone binds nothing
        (sha(FILE_TEXT), F_KCT, None, False),  # match, bound and fresh
        (sha(FILE_TEXT), F_BASE, F_KCT, False),  # match, bound, caller's write is fresh
        (sha(FILE_TEXT), F_BASE, None, "unknown"),  # match, stale binding
        (sha(FILE_TEXT), F_BASE, F_OTHER, "unknown"),  # match, stale binding and write
        (sha(EDITED_TEXT), None, None, True),  # mismatch, unbound
        (sha(EDITED_TEXT), F_KCT, None, True),  # mismatch, bound and fresh
        (sha(EDITED_TEXT), F_BASE, None, True),  # mismatch, stale binding
    ],
)
def test_board_dirty_state_baseline_binding_matrix(tmp_path, baseline, bound, written, want):
    ref, fake = after_external_write(tmp_path)  # editor FILE_TEXT, file KCT_TEXT
    ds = dirty_state(
        fake,
        ref,
        editor_baseline_sha256=baseline,
        baseline_file_sha256=bound,
        written_file_sha256=written,
    )
    assert ds.dirty is want if isinstance(want, bool) else ds.dirty == want
    assert ds.basis == "content_comparison"


def test_board_dirty_state_bound_but_file_missing_is_never_clean(tmp_path):
    ref, fake = after_external_write(tmp_path)
    Path(ref.path).unlink()
    ds = dirty_state(
        fake,
        ref,
        editor_baseline_sha256=sha(FILE_TEXT),
        baseline_file_sha256=F_KCT,
        written_file_sha256=F_KCT,
    )
    assert ds.dirty == "unknown"
