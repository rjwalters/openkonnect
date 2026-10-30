"""SES-06 ``run_kct_handoff`` against a fake IPC backend and a fake ``kct``."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from openkonnect.tools.run_kct_handoff import (
    CommandResult,
    default_runner,
    run_kct_handoff,
    validate_command,
)

from .conftest import make_session
from .test_save_before_external_write import (
    CODES,
    EDITED_TEXT,
    FILE_TEXT,
    SyncFake,
    board_on_disk,
    connection_cases,
)

KCT_TEXT = '(kicad_pcb (version 20241229) (generator "kct") (routed yes))\n'
ROUTE = ["route", "{board}", "-o", "{board}"]


class FakeKct:
    """Records the argv and does what it is told to the board file."""

    def __init__(self, write=KCT_TEXT, rc=0, stdout="", timed_out=False, during=None):
        self.write, self.rc, self.stdout, self.timed_out = write, rc, stdout, timed_out
        self.during = during
        self.calls: list[tuple[list[str], float, str | None]] = []

    def __call__(self, argv, timeout_s, cwd):
        self.calls.append((argv, timeout_s, cwd))
        if self.write is not None:
            Path(argv[2]).write_text(self.write)
        if self.during:
            self.during()
        return CommandResult(None if self.timed_out else self.rc, self.stdout, "", self.timed_out)


def handoff(session, kct=None, command=ROUTE, **kw):
    return run_kct_handoff(session, command, runner=kct or FakeKct(), kct="/bin/kct", **kw)


def names(r):
    return [(s["step"], s["name"], s["outcome"]) for s in r["steps"]]


# -- allow-list -------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        ["rm", "-rf", "{board}"],
        ["drc", "{board}"],
        ["zones", "list", "{board}"],
        "route {board} -o {board}",
        [],
        ["kct"],
        ["route", "/some/other.kicad_pcb"],
        ["route", "{board}", 3],
    ],
)
def test_not_allow_listed_is_invalid_argument_and_touches_nothing(tmp_path, command):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref)
    kct = FakeKct()
    r = handoff(make_session(fake), kct, command=command, allow_save=True)
    assert r["ok"] is False
    assert r["error"]["code"] == "invalid_argument"
    assert r["failed_step"] == "validate"
    assert fake.calls == []
    assert kct.calls == []


def test_allow_list_accepts_leading_kct_and_subcommands():
    assert validate_command(["kct", "route", "{board}"]) == ["route", "{board}"]
    assert validate_command(["zones", "fill", "{board}"])[0] == "zones"
    assert validate_command(["fix-vias", "--output={board}", "{board}"])


# -- flags that write elsewhere (issue #59) ---------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        ["route", "--auto-layers", "{board}"],
        ["route", "{board}", "--auto-layers", "-o", "{board}"],
        ["route", "--auto-l", "{board}"],
        ["route-auto", "--checkpoint", "x", "{board}"],
        ["optimize-placement", "--checkpoint=x", "{board}"],
        ["route", "{board}", "-o", "other.kicad_pcb"],
        ["route", "{board}", "-oother.kicad_pcb"],
        ["route", "{board}", "--output=other.kicad_pcb"],
        ["route", "{board}", "--output", "other.kicad_pcb"],
        ["route", "{board}", "--out", "other.kicad_pcb"],
        ["route", "{board}", "-o", "{board}.bak"],
        ["route", "{board}", "-o"],
        ["route", "{board}", "-vo", "other.kicad_pcb"],
        ["route", "{board}", "-qoother.kicad_pcb"],
        ["route", "{board}", "-vo"],
        ["route", "{board}", "-o", "{board}", "--output", "other.kicad_pcb"],
    ],
)
def test_output_redirecting_flags_are_invalid_and_touch_nothing(tmp_path, command):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref)
    kct = FakeKct()
    r = handoff(make_session(fake), kct, command=command, allow_save=True)
    assert r["ok"] is False
    assert r["error"]["code"] == "invalid_argument"
    assert r["failed_step"] == "validate"
    assert fake.calls == []
    assert kct.calls == []


@pytest.mark.parametrize(
    "command",
    [
        ["route", "{board}", "-o", "{board}"],
        ["route", "{board}", "-o{board}"],
        ["route", "{board}", "--output", "{board}"],
        ["route", "{board}", "--output={board}"],
        ["route", "{board}", "-vo", "{board}"],
        ["route", "{board}", "-qo{board}"],
        ["fix-vias", "{board}", "--c", "1"],
        ["optimize-traces", "{board}", "--ch", "0.2"],
        ["place-silk-refs", "{board}", "--a"],
        ["route", "{board}"],
    ],
)
def test_output_to_the_board_is_accepted(command):
    assert validate_command(command) == command


def test_output_to_the_board_runs_the_handoff(tmp_path):
    ref = board_on_disk(tmp_path)
    kct = FakeKct()
    command = ["route", "{board}", "--output", "{board}"]
    r = handoff(make_session(SyncFake(ref)), kct, command=command, allow_save=True)
    assert r["ok"] is True, r
    assert len(kct.calls) == 1
    assert kct.calls[0][0][1:] == ["route", ref.path, "--output", ref.path]


# -- the protocol -----------------------------------------------------------------


def test_clean_board_full_protocol(tmp_path):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref)
    kct = FakeKct(stdout=json.dumps({"routed": 3}))
    r = handoff(make_session(fake), kct)
    assert r["ok"] is True, r
    assert names(r) == [
        (1, "session_status", "ok"),
        (2, "save_before_external_write", "none"),
        (3, "kct", "ok"),
        (4, "check_command", "ok"),
        (5, "check_file_changed", "changed"),
        (6, "sync_from_disk", "reloaded"),
        (7, "report", "ok"),
    ]
    argv, _, cwd = kct.calls[0]
    assert argv == ["/bin/kct", "route", ref.path, "-o", ref.path]
    assert cwd == str(tmp_path)
    assert r["command"][0] == "kct"
    assert r["exit_code"] == 0
    assert r["output_json"] == {"routed": 3}
    assert r["final_state"]["reloaded"] is True
    assert r["steps"][5]["discarded_unsaved"] is False
    assert r["steps"][5]["snapshot_matched"] is True
    assert fake.editor == KCT_TEXT
    assert "Ctrl+Z" in r["undo_note"]


def test_no_snapshot_without_discard_refuses_unknown_at_step_6(tmp_path, monkeypatch):
    # If step 2 could not prove the editor text equal to the saved file, there is no
    # snapshot, and no recorded hash can prove the editor clean. Issue #15.
    from openkonnect.tools import run_kct_handoff as handoff_mod

    real = handoff_mod.save_before_external_write

    def no_match(*a, **kw):
        out = real(*a, **kw)
        out["documents"][0]["editor_matches_file"] = "unknown"
        return out

    monkeypatch.setattr(handoff_mod, "save_before_external_write", no_match)
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref)
    r = handoff(make_session(fake))
    assert r["ok"] is False, r
    assert r["failed_step"] == "sync_from_disk"
    assert r["error"]["code"] == "dirty_state_unknown"
    assert "discard_unsaved=true" in r["error"]["details"]["handoff_state"]
    assert "revert_board" not in fake.calls
    assert fake.editor == FILE_TEXT
    assert Path(ref.path).read_text() == KCT_TEXT


def test_human_save_then_undo_during_command_loads_the_human_save(tmp_path):
    # The documented window (docs/handoff-protocol.md, SES-06): during the kct run
    # the human saves an edit from KiCad (the file now holds it) and undoes it
    # exactly (the editor is back to the step-2 snapshot). The snapshot matches, so
    # the reload loads the human's saved file over the editor. The edit is kept on
    # disk and in the editor; the command's output, which the human's save
    # replaced, is not.
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref)

    def human_saves_then_undoes():
        Path(ref.path).write_text(EDITED_TEXT)  # save from KiCad; the editor then undoes

    r = handoff(make_session(fake), FakeKct(during=human_saves_then_undoes))
    assert r["ok"] is True, r
    assert r["steps"][5]["snapshot_matched"] is True
    assert r["steps"][5]["discarded_unsaved"] is False
    assert "revert_board" in fake.calls
    assert fake.editor == EDITED_TEXT
    assert Path(ref.path).read_text() == EDITED_TEXT


def test_unknown_dirty_needs_allow_save_and_command_never_runs(tmp_path):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref, EDITED_TEXT)
    kct = FakeKct()
    r = handoff(make_session(fake), kct)
    assert r["ok"] is False
    assert r["failed_step"] == "save_before_external_write"
    assert r["error"]["code"] == "dirty_state_unknown"
    assert kct.calls == []


def test_allow_save_then_reload_without_discard(tmp_path):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref, EDITED_TEXT)
    r = handoff(make_session(fake), allow_save=True)
    assert r["ok"] is True, r
    assert r["steps"][1]["outcome"] == "saved"
    assert r["steps"][5]["discarded_unsaved"] is False
    assert r["steps"][5]["snapshot_matched"] is True
    assert fake.editor == KCT_TEXT


def test_user_edit_during_command_refuses_at_step_6(tmp_path):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref)

    def user_edits():
        fake.editor = EDITED_TEXT  # the fake flips to dirty while kct runs

    r = handoff(make_session(fake), FakeKct(during=user_edits))
    assert r["ok"] is False
    assert r["failed_step"] == "sync_from_disk"
    assert r["error"]["code"] == "document_dirty"
    assert "command's output" in r["error"]["details"]["handoff_state"]
    assert "revert_board" not in fake.calls
    assert fake.editor == EDITED_TEXT
    assert Path(ref.path).read_text() == KCT_TEXT
    # Both the command's result and the refusal are reported, for recovery.
    assert r["exit_code"] == 0
    assert r["steps"][2]["name"] == "kct" and r["steps"][2]["outcome"] == "ok"
    assert r["steps"][2]["file_changed"] is True
    assert r["steps"][-1] == {"step": 6, "name": "sync_from_disk", "outcome": "refused"}


@pytest.mark.parametrize("allow_save", [False, True])
def test_user_edit_during_command_after_a_save_refuses_at_step_6(tmp_path, allow_save):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref, EDITED_TEXT if allow_save else None)
    later = '(kicad_pcb (version 20241229) (generator "pcbnew") (later edit))\n'

    def user_edits():
        fake.editor = later

    r = handoff(make_session(fake), FakeKct(during=user_edits), allow_save=allow_save)
    assert r["ok"] is False, r
    assert r["error"]["code"] == "document_dirty"
    assert "revert_board" not in fake.calls
    assert fake.editor == later


def test_user_edit_during_command_with_discard_reloads_and_says_so(tmp_path):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref)

    def user_edits():
        fake.editor = EDITED_TEXT

    r = handoff(make_session(fake), FakeKct(during=user_edits), discard_unsaved=True)
    assert r["ok"] is True, r
    assert r["steps"][5]["outcome"] == "reloaded"
    assert r["steps"][5]["discarded_unsaved"] is True
    assert r["steps"][5]["snapshot_matched"] is False
    assert fake.editor == KCT_TEXT


def test_snapshot_cannot_be_supplied_by_the_caller(tmp_path):
    # run_kct_handoff takes no snapshot argument, and the MCP tools do not expose one.
    import inspect

    from openkonnect.server import build_server

    ref = board_on_disk(tmp_path)
    with pytest.raises(TypeError):
        run_kct_handoff(
            make_session(SyncFake(ref)),
            ROUTE,
            runner=FakeKct(),
            kct="/bin/kct",
            handoff_snapshot_sha256="x",
        )
    assert "snapshot" not in " ".join(inspect.signature(run_kct_handoff).parameters)
    import anyio

    tools = anyio.run(build_server(make_session(SyncFake(ref))).list_tools)
    for t in tools:
        assert not any("snapshot" in p for p in (t.inputSchema.get("properties") or {})), t.name


def test_snapshot_comes_from_this_calls_own_save(tmp_path, monkeypatch):
    # Whatever the caller passes, step 6 is given the hash step 2 saved in this call.
    from openkonnect.tools import run_kct_handoff as handoff_mod

    seen = {}
    real = handoff_mod.sync_from_disk

    def spy(session, *a, **kw):
        seen.update(kw)
        return real(session, *a, **kw)

    monkeypatch.setattr(handoff_mod, "sync_from_disk", spy)
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref)

    def user_edits():
        fake.editor = EDITED_TEXT

    r = handoff(make_session(fake), FakeKct(during=user_edits))
    assert r["ok"] is False
    assert seen["handoff_snapshot_sha256"] == r["steps"][1]["editor_sha256"]
    assert seen["handoff_snapshot_sha256"] == r["steps"][1]["sha256"]


def test_handoff_passes_no_hash_that_could_prove_clean(tmp_path, monkeypatch):
    from openkonnect.tools import run_kct_handoff as handoff_mod

    seen = {}
    real = handoff_mod.sync_from_disk

    def spy(session, *a, **kw):
        seen.update(kw)
        return real(session, *a, **kw)

    monkeypatch.setattr(handoff_mod, "sync_from_disk", spy)
    ref = board_on_disk(tmp_path)
    r = handoff(make_session(SyncFake(ref)))
    assert r["ok"] is True, r
    assert set(seen) == {
        "discard_unsaved",
        "expected_hash",
        "editor_hash",
        "handoff_snapshot_sha256",
    }
    assert seen["editor_hash"] == r["steps"][1]["editor_sha256"]
    assert seen["expected_hash"] == r["steps"][2]["sha256_after"]
    assert seen["handoff_snapshot_sha256"] == r["steps"][1]["editor_sha256"]
    assert r["steps"][5]["discarded_unsaved"] is False


def raising(exc):
    def runner(argv, timeout_s, cwd):
        raise exc

    return runner


@pytest.mark.parametrize("exc", [RuntimeError("boom"), ValueError("bad value")])
def test_runner_error_is_kicad_tools_failed_without_reload(tmp_path, exc):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref)
    r = handoff(make_session(fake), raising(exc))
    assert r["ok"] is False
    assert r["failed_step"] == "kct"
    assert r["error"]["code"] == "kicad_tools_failed"
    assert r["error"]["details"]["exception"] == type(exc).__name__
    assert r["error"]["details"]["message"] == str(exc)
    assert r["steps"][-1] == {"step": 3, "name": "kct", "outcome": "runner_error"}
    assert r["command"][0] == "kct"
    assert "revert_board" not in fake.calls


def test_runner_oserror_is_failed_to_start(tmp_path):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref)
    r = handoff(make_session(fake), raising(FileNotFoundError("no kct")))
    assert r["error"]["code"] == "kicad_tools_failed"
    assert r["steps"][-1]["outcome"] == "failed_to_start"
    assert "revert_board" not in fake.calls


def test_runner_openkonnect_error_passes_through(tmp_path):
    from openkonnect.errors import ErrorCode, OpenKonnectError

    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref)
    err = OpenKonnectError(ErrorCode.TIMEOUT, "runner gave up", details={"x": 1})
    r = handoff(make_session(fake), raising(err))
    assert r["ok"] is False
    assert r["failed_step"] == "kct"
    assert r["error"] == err.to_dict()
    assert "revert_board" not in fake.calls


def test_command_failure_stops_without_reload(tmp_path):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref)
    r = handoff(make_session(fake), FakeKct(write=None, rc=2))
    assert r["ok"] is False
    assert r["failed_step"] == "kct"
    assert r["error"]["code"] == "kicad_tools_failed"
    assert r["error"]["details"] == {"exit_code": 2, "file_changed": False}
    assert names(r)[-1] == (4, "check_command", "stopped")
    assert "revert_board" not in fake.calls


def test_timeout_stops_and_does_not_reload(tmp_path):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref)
    r = handoff(make_session(fake), FakeKct(timed_out=True), timeout_s=1)
    assert r["ok"] is False
    assert r["error"]["code"] == "timeout"
    assert r["error"]["details"]["file_changed"] is True
    assert "partial" in r["error"]["message"]
    assert r["steps"][2]["outcome"] == "timeout"
    assert "revert_board" not in fake.calls
    assert fake.editor == FILE_TEXT


def test_unchanged_file_skips_reload(tmp_path):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref)
    r = handoff(make_session(fake), FakeKct(write=None))
    assert r["ok"] is True, r
    assert (5, "check_file_changed", "skipped_reload") in names(r)
    assert r["final_state"]["reloaded"] is False
    assert "revert_board" not in fake.calls


def test_schematic_is_unsupported(tmp_path):
    fake = SyncFake(board_on_disk(tmp_path))
    r = handoff(make_session(fake), documents="all")
    assert r["error"]["code"] == "unsupported_on_this_kicad"
    assert fake.calls == []


@pytest.mark.parametrize("timeout_s", [0, -1, 10_000])
def test_bad_timeout(tmp_path, timeout_s):
    r = handoff(make_session(SyncFake(board_on_disk(tmp_path))), timeout_s=timeout_s)
    assert r["error"]["code"] == "invalid_argument"


@pytest.mark.parametrize("code", CODES)
def test_connection_errors_fail_fast(tmp_path, code):
    ref = board_on_disk(tmp_path)
    _, session_kw, script = next(c for c in connection_cases(ref) if c[0] == code)
    kct = FakeKct()
    r = handoff(make_session(SyncFake(ref, **script), **session_kw), kct, allow_save=True)
    assert r["ok"] is False
    assert r["failed_step"] == "session_status"
    assert r["error"]["code"] == code
    assert kct.calls == []


def test_default_runner_kills_on_timeout():
    started = time.monotonic()
    res = default_runner([sys.executable, "-c", "import time; time.sleep(30)"], 0.5, None)
    assert res.timed_out is True
    assert res.returncode is None
    assert time.monotonic() - started < 5


def test_default_runner_captures_output():
    res = default_runner([sys.executable, "-c", "print('{\"a\": 1}')"], 10, None)
    assert res.returncode == 0
    assert json.loads(res.stdout) == {"a": 1}


# -- KiCad 10.0.7: the save matches canonically, so the handoff has its snapshot (#37) --


def kicad_board(tmp_path, **kw):
    from .test_save_before_external_write import KICAD_EDITOR, KicadSaveFake

    ref = board_on_disk(tmp_path)  # written by another tool: needs allow_save
    return ref, KicadSaveFake(ref, KICAD_EDITOR, **kw)


def test_canonical_save_takes_the_snapshot_and_reloads_without_discard(tmp_path):
    ref, fake = kicad_board(tmp_path)
    r = handoff(make_session(fake), allow_save=True)
    assert r["ok"] is True, r
    assert r["steps"][1]["outcome"] == "saved"
    assert r["steps"][1]["editor_matches_file"] is True
    assert r["steps"][1]["match_basis"] == "canonical"
    assert r["steps"][1]["editor_sha256"] != r["steps"][1]["sha256"]
    assert r["steps"][5]["outcome"] == "reloaded"
    assert r["steps"][5]["snapshot_matched"] is True
    assert r["steps"][5]["discarded_unsaved"] is False
    assert fake.editor == KCT_TEXT


def test_canonical_save_then_user_edit_during_command_is_document_dirty(tmp_path):
    from .test_save_before_external_write import KICAD_EDITED

    ref, fake = kicad_board(tmp_path)

    def user_edits():
        fake.editor = KICAD_EDITED

    r = handoff(make_session(fake), FakeKct(during=user_edits), allow_save=True)
    assert r["ok"] is False, r
    assert r["failed_step"] == "sync_from_disk"
    assert r["error"]["code"] == "document_dirty"
    assert "revert_board" not in fake.calls
    assert fake.editor == KICAD_EDITED
    assert Path(ref.path).read_text() == KCT_TEXT


def test_unlisted_version_has_no_snapshot_and_refuses_unknown(tmp_path):
    ref, fake = kicad_board(tmp_path, kicad_version="10.0.8")
    r = handoff(make_session(fake), allow_save=True)
    assert r["ok"] is False, r
    assert r["steps"][1]["editor_matches_file"] is False
    assert r["failed_step"] == "sync_from_disk"
    assert r["error"]["code"] == "dirty_state_unknown"
    assert "revert_board" not in fake.calls


def test_file_written_during_step_2_check_takes_no_snapshot(tmp_path, monkeypatch):
    # The file changes between step 2's file read and its editor comparison, so
    # the clean state is not bound to the reported sha256: step 2 refuses and
    # nothing runs (Judge on PR #48, item 2).
    from openkonnect.tools import save_before_external_write as save_mod

    from .test_save_before_external_write import (
        KICAD_EDITOR,
        KicadSaveFake,
        file_written_after_first_read,
        kicad_save_form,
    )

    ref = board_on_disk(tmp_path, kicad_save_form(KICAD_EDITOR))
    fake = KicadSaveFake(ref, KICAD_EDITOR)
    file_written_after_first_read(monkeypatch, save_mod, kicad_save_form(KICAD_EDITOR) + "\n")
    kct = FakeKct()
    r = handoff(make_session(fake), kct)
    assert r["ok"] is False, r
    assert r["failed_step"] == "save_before_external_write"
    assert r["error"]["code"] == "dirty_state_unknown"
    assert "save_board" not in fake.calls
    assert "revert_board" not in fake.calls
    assert kct.calls == []
