"""SES-05 ``sync_from_disk`` against a fake IPC backend."""

from __future__ import annotations

import inspect
import itertools
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


_SYNC_PARAMS = set(inspect.signature(sync_from_disk).parameters)


def sync_any(session, **kw):
    """``sync`` that drops keyword arguments this ``sync_from_disk`` does not take.

    The regression tests for issue #15 pass hash arguments that some versions of
    the tool accept and others do not (``editor_file_hash``). Dropping them keeps
    every version callable, so a test fails on the bug's behaviour (a wrong
    ``ok: True`` or ``dirty: False``), never on a signature error.
    """
    return sync(session, **{k: v for k, v in kw.items() if k in _SYNC_PARAMS})


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


def test_baseline_unchanged_after_write_is_unknown_without_discard(tmp_path):
    # The editor still matches its recorded hash, but it differs from the file now:
    # no recorded hash can prove it holds nothing the file lacks. Issue #15.
    ref, fake = after_external_write(tmp_path)
    r = sync(make_session(fake), editor_hash=sha(FILE_TEXT), expected_hash=sha(KCT_TEXT))
    assert r["ok"] is False, r
    assert r["error"]["code"] == "dirty_state_unknown"
    assert r["error"]["details"]["documents"][0]["state_before"]["dirty"] == "unknown"
    assert "revert_board" not in fake.calls
    assert fake.editor == FILE_TEXT


def test_baseline_unchanged_reloads_with_discard(tmp_path):
    ref, fake = after_external_write(tmp_path)
    r = sync(
        make_session(fake),
        editor_hash=sha(FILE_TEXT),
        expected_hash=sha(KCT_TEXT),
        discard_unsaved=True,
    )
    assert r["ok"] is True, r
    (doc,) = r["documents"]
    assert doc["state_before"]["dirty"] == "unknown"
    assert doc["state_before"]["dirty_basis"] == "content_comparison"
    assert doc["action"] == "reloaded"
    assert doc["reloaded"] is True
    assert doc["discarded_unsaved"] == "unknown"
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
    r = sync(make_session(fake), editor_hash=sha(FILE_TEXT), discard_unsaved=True)
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
        expected_hash=sha(KCT_TEXT),
        discard_unsaved=True,
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


# Issue #15: no caller-supplied hash may ever prove the board clean. Every test
# below goes through sync_any, so it runs against any version of the tool and
# fails only on the behaviour.


def save_then_undo(tmp_path):
    """Baseline H0 = FILE_TEXT (file hash F0). The human then saved an edit in KiCad
    (file now EDITED_TEXT) and undid back, so the editor again holds H0 while the
    file differs: the editor's state is unsaved relative to the last save."""
    ref = board_on_disk(tmp_path, EDITED_TEXT)
    return ref, SyncFake(ref, FILE_TEXT)


def assert_refused_unknown(r, fake):
    assert r["ok"] is False, r
    assert r["error"]["code"] == "dirty_state_unknown"
    assert r["error"]["details"]["documents"][0]["state_before"]["dirty"] == "unknown"
    assert "revert_board" not in fake.calls
    assert fake.editor == FILE_TEXT


@pytest.mark.parametrize("file_hash", ["garbage", sha(FILE_TEXT)], ids=["junk", "stale"])
def test_judge_probe_fresh_expected_hash_cannot_prove_clean(tmp_path, file_hash):
    # The Judge's probe on PR #22: the old editor_hash, a junk (or the real, stale)
    # editor_file_hash, and an expected_hash read fresh from the file.
    ref, fake = save_then_undo(tmp_path)
    r = sync_any(
        make_session(fake),
        editor_hash=sha(FILE_TEXT),
        editor_file_hash=file_hash,
        expected_hash=sha(EDITED_TEXT),
    )
    assert_refused_unknown(r, fake)


def test_save_then_undo_editor_hash_alone_is_unknown(tmp_path):
    ref, fake = save_then_undo(tmp_path)
    r = sync(make_session(fake), editor_hash=sha(FILE_TEXT))
    assert_refused_unknown(r, fake)


@pytest.mark.parametrize("file_hash", [None, sha(FILE_TEXT)], ids=["unbound", "stale_bound"])
def test_save_then_undo_is_unknown_and_not_reloaded(tmp_path, file_hash):
    ref, fake = save_then_undo(tmp_path)
    r = sync_any(make_session(fake), editor_hash=sha(FILE_TEXT), editor_file_hash=file_hash)
    assert_refused_unknown(r, fake)


@pytest.mark.parametrize("file_hash", [None, sha(FILE_TEXT)], ids=["unbound", "stale_bound"])
def test_save_then_undo_with_discard_reports_unknown(tmp_path, file_hash):
    ref, fake = save_then_undo(tmp_path)
    r = sync_any(
        make_session(fake),
        editor_hash=sha(FILE_TEXT),
        editor_file_hash=file_hash,
        discard_unsaved=True,
    )
    assert r["ok"] is True, r
    assert r["documents"][0]["state_before"]["dirty"] == "unknown"
    assert r["documents"][0]["discarded_unsaved"] == "unknown"
    assert fake.editor == EDITED_TEXT


def test_editor_hash_alone_after_external_write_is_unknown(tmp_path):
    ref, fake = after_external_write(tmp_path)
    r = sync(make_session(fake), editor_hash=sha(FILE_TEXT), expected_hash=sha(KCT_TEXT))
    assert_refused_unknown(r, fake)


def test_binding_without_expected_hash_after_write_is_unknown(tmp_path):
    ref, fake = after_external_write(tmp_path)
    r = sync_any(make_session(fake), editor_hash=sha(FILE_TEXT), editor_file_hash=sha(FILE_TEXT))
    assert_refused_unknown(r, fake)


# Property: over every combination of caller-supplied hash values, dirty is never
# False (and nothing is reloaded without discard_unsaved) unless the editor's
# content, read now, equals the file's content, read now.

STATES = {
    "clean": (FILE_TEXT, FILE_TEXT),  # (editor, file)
    "after_write": (FILE_TEXT, KCT_TEXT),
    "save_then_undo": (FILE_TEXT, EDITED_TEXT),
    "edited": (EDITED_TEXT, FILE_TEXT),
}
HASH_VALUES = [None, "garbage", sha(FILE_TEXT), sha(KCT_TEXT), sha(EDITED_TEXT)]
HASH_ARGS = ("editor_hash", "editor_file_hash", "expected_hash")


@pytest.mark.parametrize("state", list(STATES))
def test_no_caller_hash_combination_proves_clean(tmp_path, state):
    editor, on_disk = STATES[state]
    fresh_equal = editor == on_disk
    seen_false = False
    for values in itertools.product(HASH_VALUES, repeat=len(HASH_ARGS)):
        kw = {k: v for k, v in zip(HASH_ARGS, values, strict=True) if v is not None}
        ref = board_on_disk(tmp_path, on_disk)
        fake = SyncFake(ref, editor)
        r = sync_any(make_session(fake), **kw)
        if r["ok"]:
            st = r["documents"][0]["state_before"]
        elif r["error"]["code"] in ("dirty_state_unknown", "document_dirty"):
            st = r["error"]["details"]["documents"][0]["state_before"]
        else:
            assert r["error"]["code"] == "stale_input", (kw, r)
            st = None
        if st is not None and st["dirty"] is False:
            seen_false = True
            assert fresh_equal, (state, kw, st)
        if "revert_board" in fake.calls:
            assert fresh_equal, (state, kw, "reloaded without discard_unsaved")
    # The clean state must still be provable from the fresh read alone.
    assert seen_false == fresh_equal


# board_dirty_state over editor x file x baseline: False only for fresh equality.

BASELINES = [None, "garbage", sha(FILE_TEXT), sha(KCT_TEXT), sha(EDITED_TEXT)]


def dirty_state(fake, ref, **kw):
    def body(s):
        s.require_reachable()
        return {"ds": board_dirty_state(s, ref, **kw)}

    r = make_session(fake).run_tool(body)
    assert r["ok"] is True, r
    return r["ds"]


@pytest.mark.parametrize("state", list(STATES))
@pytest.mark.parametrize("baseline", BASELINES)
def test_board_dirty_state_false_only_for_fresh_equality(tmp_path, state, baseline):
    editor, on_disk = STATES[state]
    ref = board_on_disk(tmp_path, on_disk)
    ds = dirty_state(SyncFake(ref, editor), ref, editor_baseline_sha256=baseline)
    if editor == on_disk:
        assert ds.dirty is False
    elif baseline is not None and baseline != sha(editor):
        assert ds.dirty is True
    else:
        assert ds.dirty == "unknown"


def test_board_dirty_state_file_missing_is_never_clean(tmp_path):
    ref, fake = after_external_write(tmp_path)
    Path(ref.path).unlink()
    ds = dirty_state(fake, ref, editor_baseline_sha256=sha(FILE_TEXT))
    assert ds.dirty == "unknown"


def test_public_sync_with_the_saved_editor_hash_stays_unknown(tmp_path):
    # The handoff's snapshot rule is internal: the public tool, given the saved
    # editor_sha256 as editor_hash after an external write, still refuses with
    # dirty_state_unknown. Issue #15.
    from openkonnect.tools.save_before_external_write import save_before_external_write

    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref)
    session = make_session(fake)
    saved = session.run_tool(lambda s: save_before_external_write(s, "board"))
    Path(ref.path).write_text(KCT_TEXT)
    r = sync(session, editor_hash=saved["documents"][0]["editor_sha256"])
    assert r["ok"] is False, r
    assert r["error"]["code"] == "dirty_state_unknown"
    assert "revert_board" not in fake.calls
    assert fake.editor == FILE_TEXT


def test_snapshot_never_relaxes_a_changed_editor(tmp_path):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref, EDITED_TEXT)
    Path(ref.path).write_text(KCT_TEXT)
    snap = sha(FILE_TEXT)
    r = sync(make_session(fake), editor_hash=snap, handoff_snapshot_sha256=snap)
    assert r["ok"] is False, r
    assert r["error"]["code"] == "document_dirty"
    assert "revert_board" not in fake.calls


# -- the canonical match (issue #37) ------------------------------------------------


def test_board_dirty_state_canonical_match_is_false_only_on_the_measured_version(tmp_path):
    from .test_save_before_external_write import KICAD_EDITOR, kicad_save_form

    ref = board_on_disk(tmp_path, kicad_save_form(KICAD_EDITOR))
    ds = dirty_state(SyncFake(ref, KICAD_EDITOR), ref)
    assert ds.dirty is False
    assert ds.match_basis == "canonical"
    assert ds.record()["match_basis"] == "canonical"
    for version in ("10.0.6", "10.0.8", "11.0.0"):
        ds = dirty_state(SyncFake(ref, KICAD_EDITOR, kicad_version=version), ref)
        assert ds.dirty == "unknown", version
        assert ds.match_basis is None


@pytest.mark.parametrize("baseline", [None, "garbage", sha(KCT_TEXT)])
def test_board_dirty_state_canonical_difference_is_never_false(tmp_path, baseline):
    from .test_save_before_external_write import KICAD_EDITED, KICAD_EDITOR, kicad_save_form

    ref = board_on_disk(tmp_path, kicad_save_form(KICAD_EDITOR))
    ds = dirty_state(SyncFake(ref, KICAD_EDITED), ref, editor_baseline_sha256=baseline)
    assert ds.dirty is not False


def test_caller_hash_still_never_gives_false_on_the_measured_version(tmp_path):
    # Editor equals a caller hash of itself, file differs by more than the ignore list.
    from .test_save_before_external_write import KICAD_EDITED, KICAD_EDITOR, kicad_save_form

    ref = board_on_disk(tmp_path, kicad_save_form(KICAD_EDITOR))
    ds = dirty_state(SyncFake(ref, KICAD_EDITED), ref, editor_baseline_sha256=sha(KICAD_EDITED))
    assert ds.dirty == "unknown"


def test_public_sync_after_a_kicad_save_reloads_without_discard(tmp_path):
    # Curator question 3: a canonical match gives dirty false in every caller, so a
    # plain sync_from_disk after the user saved from KiCad reloads (here the file is
    # the KiCad save itself, unchanged).
    from .test_save_before_external_write import KICAD_EDITOR, kicad_save_form

    ref = board_on_disk(tmp_path, kicad_save_form(KICAD_EDITOR))
    fake = SyncFake(ref, KICAD_EDITOR)
    r = sync(make_session(fake))
    assert r["ok"] is True, r
    assert r["documents"][0]["state_before"]["dirty"] is False
    assert r["documents"][0]["discarded_unsaved"] is False
