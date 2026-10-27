"""LAY-01 ``board_summary`` against canned IPC responses."""

from __future__ import annotations

from openkonnect.tools.board_summary import board_summary

from .conftest import CANNED_BOARD, FakeBackend, board_ref, make_session


def test_summary_counts_layers_outline_and_dirty():
    backend = FakeBackend(open_boards=[[board_ref("/p", "demo")]])
    r = make_session(backend).run_tool(board_summary)
    assert r["ok"] is True
    assert r["source"] == "live_editor"
    assert r["board"] == {"path": "/p/demo.kicad_pcb", "project_dir": "/p"}
    assert r["title_block"]["title"] == "Fixture"
    assert r["layers"] == {"copper": 2, "total": 9}
    assert r["counts"] == {
        "footprints": 3,
        "nets": 4,
        "tracks": 6,
        "track_segments": 5,
        "track_arcs": 1,
        "vias": 2,
        "zones": 2,
        "rule_areas": 1,
    }
    o = r["outline"]
    assert o["present"] is True
    assert o["width_mm"] == 30.0
    assert o["height_mm"] == 20.0
    assert o["frame"] == "file"
    assert o["origin_mm"] == {"x": 100.0, "y": 50.0}
    assert r["dirty"] == "unknown"
    assert r["dirty_basis"] == "none"
    # Reachability is checked before the board is read (SES-07).
    assert backend.calls[:2] == ["ping", "open_boards"]


def test_summary_without_outline():
    data = dict(CANNED_BOARD, outline_nm=None, edge_cuts_shapes=0)
    backend = FakeBackend(open_boards=[[board_ref()]], board_data=data)
    r = make_session(backend).run_tool(board_summary)
    assert r["ok"] is True
    assert r["outline"]["present"] is False


def test_summary_never_reads_the_file_when_unreachable():
    backend = FakeBackend()
    r = make_session(backend, sockets=[], running=False).run_tool(board_summary)
    assert r["ok"] is False
    assert "board_data" not in backend.calls
