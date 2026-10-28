"""SES-01 ``session_status`` against a fake IPC backend."""

from __future__ import annotations

import os
import time

import pytest
from kipy.errors import ApiError
from kipy.proto.common import ApiStatusCode
from kipy.proto.common.types import DocumentSpecifier, DocumentType

from openkonnect.backend import BoardRef, KipyBackend
from openkonnect.tools.session_status import session_status

from .conftest import FakeBackend, board_ref, busy, make_session


def status(session):
    return session.run_tool(session_status)


def test_nothing_running_is_data_not_error():
    r = status(make_session(sockets=[], running=False))
    assert r["ok"] is True
    assert r["kicad_running"] is False
    assert r["ipc_reachable"] is False
    assert r["state"] == "kicad_not_running"
    assert r["reason"]["code"] == "kicad_not_running"
    assert r["documents"] == []
    assert r["capabilities"]["board_summary"]["supported"] is False


def test_api_disabled_is_data_not_error():
    r = status(make_session(sockets=[], running=True, api_cfg=False))
    assert r["ok"] is True
    assert r["kicad_running"] is True
    assert r["ipc_reachable"] is False
    assert r["state"] == "ipc_disabled"
    assert "Enable KiCad API" in r["reason"]["message"]


def test_unknown_process_state_is_reported_as_unknown():
    r = status(make_session(sockets=[], running=None))
    assert r["ok"] is True
    assert r["kicad_running"] == "unknown"


def test_board_open_dirty_unknown(tmp_path):
    (tmp_path / "demo.kicad_sch").write_text("(kicad_sch)")
    ref = board_ref(str(tmp_path), "demo")
    r = status(make_session(FakeBackend(open_boards=[[ref]])))
    assert r["ok"] is True
    assert r["kicad_running"] is True
    assert r["ipc_reachable"] is True
    assert r["kicad_version"] == "10.0.7"
    assert r["api_version"]
    assert r["instance"] == {"socket": "ipc:///tmp/kicad/api.sock", "pid": None, "token": "tok-1"}
    assert r["project_dir"] == str(tmp_path)
    board, sch = r["documents"]
    assert board == {
        "kind": "board",
        "path": str(tmp_path / "demo.kicad_pcb"),
        "path_basis": "kicad_ipc",
        "open": True,
        "dirty": "unknown",
        "dirty_basis": "none",
    }
    assert sch["kind"] == "schematic"
    assert sch["path"] == str(tmp_path / "demo.kicad_sch")
    assert sch["path_basis"] == "inferred_from_project"
    assert sch["exists_on_disk"] is True
    assert sch["open"] == "unknown"
    assert sch["dirty"] == "unknown"
    assert sch["dirty_basis"] == "none"
    assert r["capabilities"]["board_summary"]["supported"] is True
    assert r["capabilities"]["dirty_state_query"]["supported"] is False


def test_schematic_inferred_even_when_missing_on_disk(tmp_path):
    r = status(make_session(FakeBackend(open_boards=[[board_ref(str(tmp_path), "x")]])))
    sch = r["documents"][1]
    assert sch["path_basis"] == "inferred_from_project"
    assert sch["exists_on_disk"] is False


def test_no_board_open():
    r = status(make_session(FakeBackend(open_boards=[[]])))
    assert r["ok"] is True
    assert r["ipc_reachable"] is True
    assert r["documents"] == []
    assert r["project_dir"] is None
    cap = r["capabilities"]["board_summary"]
    assert cap == {"supported": False, "reason": "No board is open.", "error_code": "no_document"}


def test_busy_is_an_error_after_bounded_retries():
    r = status(make_session(FakeBackend(ping=busy()), busy_retries=2))
    assert r["ok"] is False
    assert r["error"]["code"] == "kicad_busy"
    assert r["meta"]["busy_retries"] == 2


# -- #34: KiCad's startup window ----------------------------------------------------
#
# Just after the PCB Editor starts, KiCad 10.0.7 answers GetOpenDocuments with a
# document whose board_filename is still "" while project.path is already set.
# Joining those gave the project directory ("/proj/") as the board path.


def not_ready() -> ApiError:
    return ApiError("board document not fully loaded", code=ApiStatusCode.AS_NOT_READY)


class _StubKiCad:
    def __init__(self, *docs: DocumentSpecifier) -> None:
        self.docs = list(docs)

    def get_open_documents(self, doc_type: int) -> list[DocumentSpecifier]:
        assert doc_type == DocumentType.DOCTYPE_PCB
        return self.docs


def _kipy_backend(*docs: DocumentSpecifier) -> KipyBackend:
    backend = object.__new__(KipyBackend)
    backend.socket = "ipc:///tmp/kicad/api.sock"
    backend._kicad = _StubKiCad(*docs)
    return backend


def _spec(board_filename: str, project_path: str = "/proj", name: str = "demo"):
    doc = DocumentSpecifier(type=DocumentType.DOCTYPE_PCB, board_filename=board_filename)
    doc.project.path = project_path
    doc.project.name = name
    return doc


def test_startup_window_empty_filename_with_project_is_not_ready():
    backend = _kipy_backend(_spec("", "/proj", "demo"))
    with pytest.raises(ApiError) as exc:
        backend.open_boards()
    assert exc.value.code == ApiStatusCode.AS_NOT_READY


def test_new_unsaved_board_is_a_board_with_no_path_not_not_ready():
    # A PCB Editor launched with no file settles at ("", "", ""): not a startup
    # state, so it must not be retried into a permanent kicad_busy.
    (ref,) = _kipy_backend(_spec("", "", "")).open_boards()
    assert ref.path is None
    assert ref.file_name == ""
    assert ref.project_dir is None
    assert ref.project_name is None


def test_upper_case_extension_is_a_board_path():
    (ref,) = _kipy_backend(_spec("Board.KICAD_PCB")).open_boards()
    assert ref.path == os.path.join("/proj", "Board.KICAD_PCB")


@pytest.mark.parametrize("file_name", ["demo.kicad_pro", "demo"])
def test_other_non_empty_name_is_reported_as_given_not_retried(file_name):
    # Only "" was observed to mean "not loaded yet"; anything else is KiCad's
    # answer, reported as-is (joined to the project), never a permanent busy.
    (ref,) = _kipy_backend(_spec(file_name)).open_boards()
    assert ref.path == os.path.join("/proj", file_name)


def test_complete_document_spec_joins_project_dir():
    (ref,) = _kipy_backend(_spec("demo.kicad_pcb")).open_boards()
    assert ref.path == os.path.join("/proj", "demo.kicad_pcb")
    assert ref.file_name == "demo.kicad_pcb"
    assert ref.project_dir == "/proj"
    assert ref.project_name == "demo"


def test_one_incomplete_document_makes_the_whole_answer_not_ready():
    backend = _kipy_backend(_spec("demo.kicad_pcb"), _spec("", "/other", "other"))
    with pytest.raises(ApiError) as exc:
        backend.open_boards()
    assert exc.value.code == ApiStatusCode.AS_NOT_READY


def test_board_still_loading_is_retried_then_reported(tmp_path):
    ref = board_ref(str(tmp_path), "demo")
    backend = FakeBackend(open_boards=[not_ready(), not_ready(), [ref]])
    r = status(make_session(backend))
    assert r["ok"] is True
    assert r["documents"][0]["path"] == str(tmp_path / "demo.kicad_pcb")
    assert r["documents"][0]["path_basis"] == "kicad_ipc"
    assert r["meta"]["busy_retries"] == 2


def test_board_that_never_finishes_loading_is_kicad_busy_within_bound():
    backend = FakeBackend(open_boards=[not_ready()])
    started = time.monotonic()
    r = status(make_session(backend, timeout_s=2.0))
    elapsed = time.monotonic() - started
    assert r["ok"] is False
    assert r["error"]["code"] == "kicad_busy"
    assert r["error"]["retryable"] is True
    assert "documents" not in r
    assert r["meta"]["busy_retries"] == 3
    assert elapsed < 2.0


# -- a new board that has never been saved ---------------------------------------


def unsaved_ref() -> BoardRef:
    return BoardRef(path=None, file_name="", project_dir=None, project_name=None)


def test_unsaved_board_status_always_answers_with_no_path():
    r = status(make_session(FakeBackend(open_boards=[[unsaved_ref()]])))
    assert r["ok"] is True
    assert r["project_dir"] is None
    (doc,) = r["documents"]
    assert doc["kind"] == "board"
    assert doc["path"] is None
    assert doc["path_basis"] == "unsaved"
    assert doc["open"] is True
    caps = r["capabilities"]
    assert caps["board_summary"] == {"supported": True}
    for tool in ("save_before_external_write", "sync_from_disk", "run_kct_handoff"):
        assert caps[tool]["supported"] is False
        assert caps[tool]["error_code"] == "no_document"
        assert "never been saved" in caps[tool]["reason"]


@pytest.mark.parametrize("tool", ["save", "sync", "kct"])
def test_file_tools_refuse_an_unsaved_board_as_no_document(tool):
    from openkonnect.tools.run_kct_handoff import run_kct_handoff
    from openkonnect.tools.save_before_external_write import save_before_external_write
    from openkonnect.tools.sync_from_disk import sync_from_disk

    fake = FakeBackend(open_boards=[[unsaved_ref()]])
    session = make_session(fake)
    if tool == "save":
        r = session.run_tool(lambda s: save_before_external_write(s, allow_save=True))
    elif tool == "sync":
        r = session.run_tool(lambda s: sync_from_disk(s))
    else:

        def runner(*a):  # pragma: no cover - must not run
            raise AssertionError("kct ran on an unsaved board")

        r = run_kct_handoff(
            session, ["route", "{board}"], runner=runner, kct="/bin/kct", allow_save=True
        )
    assert r["ok"] is False
    assert r["error"]["code"] == "no_document"
    assert r["error"]["retryable"] is False
    assert r["error"]["details"]["saved"] is False
    assert "never been saved" in r["error"]["message"]
