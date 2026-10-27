"""SES-05 ``sync_from_disk`` against a fake IPC backend."""

from __future__ import annotations

from pathlib import Path

import pytest
from kipy.errors import ApiError
from kipy.proto.common import ApiStatusCode

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
    r = sync(make_session(fake), editor_hash=sha(FILE_TEXT), expected_hash=sha(KCT_TEXT))
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
    r = sync(make_session(fake), editor_hash=sha(FILE_TEXT))
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
    r = sync(make_session(fake), editor_hash=sha(FILE_TEXT))
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
