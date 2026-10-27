"""SES-01 ``session_status`` against a fake IPC backend."""

from __future__ import annotations

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
