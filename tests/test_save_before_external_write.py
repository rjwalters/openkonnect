"""SES-04 ``save_before_external_write`` against a fake IPC backend.

Also defines :class:`SyncFake`, the file-sync fake that ``test_sync_from_disk``
and ``test_run_kct_handoff`` share. It extends the conftest fake with an editor
buffer: ``board_text`` returns it, ``save_board`` writes it to the board's
file, ``revert_board`` reads the file back into it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from kipy.errors import ApiError
from kipy.proto.common import ApiStatusCode

from openkonnect.session import DirtyState
from openkonnect.tools import save_before_external_write as save_mod
from openkonnect.tools.save_before_external_write import save_before_external_write

from .conftest import FakeBackend, board_ref, busy, make_session, timed_out, token_mismatch

FILE_TEXT = '(kicad_pcb (version 20241229) (generator "pcbnew"))\n'
EDITED_TEXT = '(kicad_pcb (version 20241229) (generator "pcbnew") (edited yes))\n'


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class SyncFake(FakeBackend):
    """A fake PCB editor holding ``editor`` text for one board."""

    def __init__(
        self,
        ref,
        editor: str | None = None,
        *,
        text_supported: bool = True,
        save_writes: bool = True,
        save_error: BaseException | None = None,
        revert_error: BaseException | None = None,
        **script: Any,
    ) -> None:
        script.setdefault("open_boards", [[ref]])
        super().__init__(**script)
        self.ref = ref
        path = Path(ref.path)
        self.editor = editor if editor is not None else path.read_text()
        self.text_supported = text_supported
        self.save_writes = save_writes
        self.save_error = save_error
        self.revert_error = revert_error

    def board_text(self, ref) -> str:
        self.calls.append("board_text")
        if not self.text_supported:
            raise ApiError("not implemented", code=ApiStatusCode.AS_UNIMPLEMENTED)
        return self.editor

    def save_board(self, ref) -> None:
        self.calls.append("save_board")
        if self.save_error is not None:
            raise self.save_error
        if self.save_writes:
            Path(ref.path).write_text(self.editor)

    def revert_board(self, ref) -> None:
        self.calls.append("revert_board")
        if self.revert_error is not None:
            raise self.revert_error
        self.editor = Path(ref.path).read_text()


def board_on_disk(tmp_path: Path, text: str = FILE_TEXT, name: str = "demo"):
    (tmp_path / f"{name}.kicad_pcb").write_text(text)
    return board_ref(str(tmp_path), name)


def save(session, **kw):
    return session.run_tool(lambda s: save_before_external_write(s, **kw))


# -- the tri-state against SES-04's table ------------------------------------------


def test_clean_board_is_left_alone(tmp_path):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref)
    r = save(make_session(fake))
    assert r["ok"] is True, r
    (doc,) = r["documents"]
    assert doc["state_before"]["dirty"] is False
    assert doc["state_before"]["dirty_basis"] == "content_comparison"
    assert doc["action"] == "none"
    assert doc["sha256"] == sha(FILE_TEXT)
    assert doc["editor_sha256"] == sha(FILE_TEXT)
    assert "save_board" not in fake.calls


def test_unknown_without_allow_save_refuses_and_saves_nothing(tmp_path):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref, EDITED_TEXT)
    r = save(make_session(fake))
    assert r["ok"] is False
    assert r["error"]["code"] == "dirty_state_unknown"
    assert r["error"]["details"]["flag"] == "allow_save"
    (doc,) = r["error"]["details"]["documents"]
    assert doc["action"] == "refused"
    assert doc["state_before"]["dirty"] == "unknown"
    assert doc["state_before"]["dirty_basis"] == "none"
    assert "save_board" not in fake.calls
    assert Path(ref.path).read_text() == FILE_TEXT


def test_dirty_without_allow_save_is_document_dirty(tmp_path, monkeypatch):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref, EDITED_TEXT)
    monkeypatch.setattr(
        save_mod,
        "board_dirty_state",
        lambda s, r: DirtyState(True, "content_comparison", "edited", sha(EDITED_TEXT)),
    )
    r = save(make_session(fake))
    assert r["error"]["code"] == "document_dirty"
    assert "save_board" not in fake.calls


@pytest.mark.parametrize("dirty", [True, "unknown"])
def test_dirty_or_unknown_with_allow_save_saves_and_verifies(tmp_path, monkeypatch, dirty):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref, EDITED_TEXT)
    if dirty is True:
        monkeypatch.setattr(
            save_mod,
            "board_dirty_state",
            lambda s, r: DirtyState(True, "content_comparison", "edited", sha(EDITED_TEXT)),
        )
    r = save(make_session(fake), allow_save=True)
    assert r["ok"] is True, r
    (doc,) = r["documents"]
    assert doc["state_before"]["dirty"] == dirty
    assert doc["action"] == "saved"
    assert doc["file_changed"] is True
    assert doc["sha256"] == sha(EDITED_TEXT)
    assert doc["editor_sha256"] == sha(EDITED_TEXT)
    assert doc["editor_matches_file"] is True
    assert Path(ref.path).read_text() == EDITED_TEXT


@pytest.mark.parametrize(
    ("editor", "on_disk"),
    [(FILE_TEXT, FILE_TEXT), (EDITED_TEXT, FILE_TEXT), (FILE_TEXT, EDITED_TEXT)],
)
def test_clean_only_by_byte_identity_with_the_file(tmp_path, editor, on_disk):
    # No caller hash reaches this check, so False needs the editor text to equal the file.
    ref = board_on_disk(tmp_path, on_disk)
    r = save(make_session(SyncFake(ref, editor)))
    states = [d["state_before"] for d in r.get("documents") or r["error"]["details"]["documents"]]
    for st in states:
        assert (st["dirty"] is False) == (editor == on_disk)


def test_next_does_not_offer_a_hash_as_a_clean_proof(tmp_path):
    ref = board_on_disk(tmp_path)
    r = save(make_session(SyncFake(ref)))
    assert "editor_file_hash" not in r["next"]
    assert "cannot prove the board clean" in r["next"]


def test_text_not_served_is_unknown_with_basis_none(tmp_path):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref, text_supported=False)
    r = save(make_session(fake))
    assert r["error"]["code"] == "dirty_state_unknown"
    assert r["error"]["details"]["documents"][0]["state_before"]["dirty_basis"] == "none"
    r = save(make_session(fake), allow_save=True)
    assert r["ok"] is True, r
    assert r["documents"][0]["editor_matches_file"] == "unknown"
    assert r["documents"][0]["editor_sha256"] is None


def test_save_success_with_unchanged_file_is_a_failure(tmp_path):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref, EDITED_TEXT, save_writes=False)
    r = save(make_session(fake), allow_save=True)
    assert r["ok"] is False
    assert r["error"]["code"] == "save_failed"
    assert r["error"]["details"]["sha256_before"] == r["error"]["details"]["sha256_after"]


def test_save_rejected_by_kicad(tmp_path):
    ref = board_on_disk(tmp_path)
    err = ApiError("file is read-only", code=ApiStatusCode.AS_BAD_REQUEST)
    fake = SyncFake(ref, EDITED_TEXT, save_error=err)
    r = save(make_session(fake), allow_save=True)
    assert r["error"]["code"] == "save_failed"
    assert "read-only" in r["error"]["details"]["kicad_message"]


# -- schematic ----------------------------------------------------------------------


@pytest.mark.parametrize("documents", ["schematic", "all", ["board", "schematic"]])
def test_schematic_in_request_is_unsupported(tmp_path, documents):
    ref = board_on_disk(tmp_path)
    fake = SyncFake(ref, EDITED_TEXT)
    r = save(make_session(fake), documents=documents, allow_save=True)
    assert r["error"]["code"] == "unsupported_on_this_kicad"
    assert r["error"]["details"]["min_kicad"] == "11"
    assert r["error"]["details"]["flag"] == "schematic_unverified_ok"
    assert "save_board" not in fake.calls


def test_schematic_unverified_ok_records_the_assertion(tmp_path):
    ref = board_on_disk(tmp_path)
    (tmp_path / "demo.kicad_sch").write_text("(kicad_sch)")
    r = save(make_session(SyncFake(ref)), documents="all", schematic_unverified_ok=True)
    assert r["ok"] is True, r
    board, sch = sorted(r["documents"], key=lambda d: d["kind"])
    assert board["action"] == "none"
    assert sch["kind"] == "schematic"
    assert sch["action"] == "none"
    assert "schematic_unverified_ok" in sch["caller_asserted"]
    assert sch["sha256"] == sha("(kicad_sch)")
    assert r["schematic_unverified_ok"] is True


def test_bad_documents_argument(tmp_path):
    r = save(make_session(SyncFake(board_on_disk(tmp_path))), documents="footprint")
    assert r["error"]["code"] == "invalid_argument"


# -- SES-07 fail fast ---------------------------------------------------------------


def connection_cases(ref):
    return [
        ("kicad_not_running", dict(sockets=[], running=False), {}),
        ("ipc_disabled", dict(sockets=[], running=True, api_cfg=False), {}),
        ("ipc_unreachable", dict(sockets=[], running=None), {}),
        ("kicad_busy", dict(busy_retries=1), dict(ping=busy())),
        ("timeout", {}, dict(ping=timed_out())),
        ("instance_changed", {}, dict(open_boards=token_mismatch())),
        ("no_document", {}, dict(open_boards=[[]])),
    ]


CODES = [c[0] for c in connection_cases(None)]


@pytest.mark.parametrize("code", CODES)
def test_connection_errors_fail_fast(tmp_path, code):
    ref = board_on_disk(tmp_path)
    _, session_kw, script = next(c for c in connection_cases(ref) if c[0] == code)
    fake = SyncFake(ref, EDITED_TEXT, **script)
    r = save(make_session(fake, **session_kw), allow_save=True)
    assert r["ok"] is False
    assert r["error"]["code"] == code
    assert "save_board" not in fake.calls


# -- KiCad 10.0.7's save form: equal by the strict token comparison (issue #37) ------

# The editor text repeats the header inside each footprint and writes short lists on
# one line; KiCad's save drops those header lines and breaks the lists (issue #23).
KICAD_EDITOR = (
    '(kicad_pcb (version 20260206) (generator "pcbnew")\n'
    '\t(footprint "f" (version 20260206) (generator "pcbnew") (generator_version "10.0")\n'
    "\t\t(at 110 60) (font (size 1 1) (thickness 0.15))))\n"
)
KICAD_EDITED = KICAD_EDITOR.replace("(at 110 60)", "(at 111 60)")


def kicad_save_form(text: str) -> str:
    """How KiCad 10.0.7's save writes the editor ``text`` (shape of the #23 record)."""
    return text.replace(
        ' (version 20260206) (generator "pcbnew") (generator_version "10.0")', ""
    ).replace(
        " (font (size 1 1) (thickness 0.15))",
        "\n\t\t(font\n\t\t\t(size 1 1)\n\t\t\t(thickness 0.15)\n\t\t)",
    )


class KicadSaveFake(SyncFake):
    """A fake editor whose save writes KiCad 10.0.7's file form, not the editor text."""

    def save_board(self, ref) -> None:
        self.calls.append("save_board")
        Path(ref.path).write_text(kicad_save_form(self.editor))


def test_kicad_save_form_is_canonically_equal_but_not_byte_equal():
    from openkonnect.kicad_text import same_document

    saved = kicad_save_form(KICAD_EDITOR)
    assert saved != KICAD_EDITOR
    assert same_document(KICAD_EDITOR, saved.encode())
    assert not same_document(KICAD_EDITED, saved.encode())


def test_saved_board_matches_canonically_on_the_measured_version(tmp_path):
    ref = board_on_disk(tmp_path)  # written by another tool: unknown before the save
    fake = KicadSaveFake(ref, KICAD_EDITOR)
    r = save(make_session(fake), allow_save=True)
    assert r["ok"] is True, r
    (doc,) = r["documents"]
    assert doc["action"] == "saved"
    assert doc["editor_matches_file"] is True
    assert doc["match_basis"] == "canonical"
    assert doc["editor_sha256"] == sha(KICAD_EDITOR)
    assert doc["sha256"] == sha(kicad_save_form(KICAD_EDITOR)) != doc["editor_sha256"]


def test_byte_match_reports_match_basis_bytes(tmp_path):
    ref = board_on_disk(tmp_path)
    r = save(make_session(SyncFake(ref, EDITED_TEXT)), allow_save=True)
    assert r["documents"][0]["match_basis"] == "bytes"
    r = save(make_session(SyncFake(ref)))
    assert r["documents"][0]["action"] == "none"
    assert r["documents"][0]["match_basis"] == "bytes"
    assert r["documents"][0]["state_before"]["match_basis"] == "bytes"


def test_board_saved_by_kicad_is_clean_on_the_measured_version(tmp_path):
    ref = board_on_disk(tmp_path, kicad_save_form(KICAD_EDITOR))
    fake = KicadSaveFake(ref, KICAD_EDITOR)
    r = save(make_session(fake))
    assert r["ok"] is True, r
    (doc,) = r["documents"]
    assert doc["state_before"]["dirty"] is False
    assert doc["state_before"]["match_basis"] == "canonical"
    assert doc["action"] == "none"
    assert doc["match_basis"] == "canonical"
    assert "save_board" not in fake.calls


@pytest.mark.parametrize(
    "version", ["10.0.6", "10.0.8", "11.0.0", ApiError("no", code=ApiStatusCode.AS_UNIMPLEMENTED)]
)
def test_unlisted_kicad_version_keeps_byte_equality(tmp_path, version):
    ref = board_on_disk(tmp_path, kicad_save_form(KICAD_EDITOR))
    fake = KicadSaveFake(ref, KICAD_EDITOR, kicad_version=version)
    r = save(make_session(fake))
    assert r["ok"] is False, r
    assert r["error"]["code"] == "dirty_state_unknown"
    assert "save_board" not in fake.calls
    r = save(make_session(fake), allow_save=True)
    assert r["ok"] is True, r
    (doc,) = r["documents"]
    assert doc["editor_matches_file"] is False
    assert doc["match_basis"] is None


def test_edited_editor_is_not_clean_on_the_measured_version(tmp_path):
    ref = board_on_disk(tmp_path, kicad_save_form(KICAD_EDITOR))
    r = save(make_session(KicadSaveFake(ref, KICAD_EDITED)))
    assert r["ok"] is False
    assert r["error"]["code"] == "dirty_state_unknown"


def test_file_rewritten_after_the_save_check_is_not_matched(tmp_path, monkeypatch):
    # The match must be against the bytes whose hash the result reports as sha256.
    ref = board_on_disk(tmp_path)
    fake = KicadSaveFake(ref, KICAD_EDITOR)
    real = save_mod.read_board_text

    def read_then_third_party_writes(session, r):
        text = real(session, r)
        Path(r.path).write_text(kicad_save_form(KICAD_EDITOR) + "\n")  # same tokens, new bytes
        return text

    monkeypatch.setattr(save_mod, "read_board_text", read_then_third_party_writes)
    r = save(make_session(fake), allow_save=True)
    assert r["ok"] is True, r
    assert r["documents"][0]["editor_matches_file"] is False
    assert r["documents"][0]["match_basis"] is None


def file_written_after_first_read(monkeypatch, module, new_text: str) -> None:
    """Patch ``module.file_state``: the first read returns, then a third party writes."""
    real = module.file_state
    done = []

    def read_then_third_party_writes(path):
        fs = real(path)
        if not done:
            done.append(True)
            Path(path).write_text(new_text)
        return fs

    monkeypatch.setattr(module, "file_state", read_then_third_party_writes)


@pytest.mark.parametrize(
    "first, second, editor",
    [
        # Canonical: both file forms equal the editor token for token, bytes differ.
        pytest.param(
            kicad_save_form(KICAD_EDITOR),
            kicad_save_form(KICAD_EDITOR) + "\n",
            KICAD_EDITOR,
            id="canonical",
        ),
        # Bytes: the file only becomes byte-identical to the editor after the first read.
        pytest.param(EDITED_TEXT, FILE_TEXT, FILE_TEXT, id="bytes"),
    ],
)
def test_file_written_between_the_two_phase_1_reads_is_not_clean(
    tmp_path, monkeypatch, first, second, editor
):
    # Phase 1 reads the file (the reported sha256), then board_dirty_state reads
    # the editor and the file again. A match proven on the second read must not
    # be reported against the first read's hash (Judge on PR #48, item 2).
    ref = board_on_disk(tmp_path, first)
    fake = KicadSaveFake(ref, editor)
    file_written_after_first_read(monkeypatch, save_mod, second)
    r = save(make_session(fake))
    assert r["ok"] is False, r
    assert r["error"]["code"] == "dirty_state_unknown"
    assert "save_board" not in fake.calls
    assert Path(ref.path).read_text() == second


def test_no_save_result_reports_the_hash_of_the_compared_bytes(tmp_path, monkeypatch):
    # Same race with allow_save: the board is saved rather than reported clean,
    # and the reported sha256 is the hash of the bytes the match was proven on.
    ref = board_on_disk(tmp_path, kicad_save_form(KICAD_EDITOR))
    fake = KicadSaveFake(ref, KICAD_EDITOR)
    file_written_after_first_read(monkeypatch, save_mod, kicad_save_form(KICAD_EDITOR) + "\n")
    r = save(make_session(fake), allow_save=True)
    assert r["ok"] is True, r
    (doc,) = r["documents"]
    assert doc["state_before"]["dirty"] == "unknown"
    assert doc["action"] == "saved"
    assert doc["editor_matches_file"] is True
    assert doc["sha256"] == sha(Path(ref.path).read_text()) == sha(kicad_save_form(KICAD_EDITOR))
