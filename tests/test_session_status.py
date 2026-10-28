"""SES-01 ``session_status`` against a fake IPC backend."""

from __future__ import annotations

import os
import time

import pytest
from kipy.errors import ApiError
from kipy.proto.common import ApiStatusCode
from kipy.proto.common.types import DocumentSpecifier, DocumentType

from openkonnect.backend import KipyBackend
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


@pytest.mark.parametrize("file_name", ["", "demo.kicad_pro", "demo"])
def test_incomplete_document_spec_is_not_ready_not_a_path(file_name):
    backend = _kipy_backend(_spec(file_name))
    with pytest.raises(ApiError) as exc:
        backend.open_boards()
    assert exc.value.code == ApiStatusCode.AS_NOT_READY


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
