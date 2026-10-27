"""SES-10 live: select in KiCad, pass the references through ``kct``, and land on the same items.

Never runs in CI. Setup: start KiCad 10 with 'Enable KiCad API' on, and open
``tests/fixtures/selection.kicad_pcb`` (unmodified) in the PCB editor. Then
run ``uv run pytest -m live_kicad -rs``. Without a reachable KiCad, the
fixture open, or ``kct``, this skips.

The test sets the starting selection itself, through the backend (there is no
``select_items`` tool yet, SES-13), and restores an empty selection after.
It never edits or saves the board.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from openkonnect.session import file_state
from openkonnect.tools.get_selection import get_selection
from openkonnect.tools.run_kct_handoff import find_kct
from openkonnect.tools.show_findings import show_findings

from ..conftest import SELECTION_BOARD

pytestmark = pytest.mark.live_kicad


@pytest.fixture
def board(live_session):
    refs = live_session.run_tool(lambda s: {"boards": s.call(lambda b: b.open_boards())})
    boards = refs.get("boards") or []
    match = [r for r in boards if os.path.basename(r.path) == os.path.basename(SELECTION_BOARD)]
    if not match:
        names = [os.path.basename(r.path) for r in boards]
        pytest.skip(f"live_kicad: open {SELECTION_BOARD} in KiCad (open boards: {names})")
    if find_kct() is None:
        pytest.skip("live_kicad: kct (kicad-tools) not found")
    ref = match[0]
    if file_state(ref.path)["sha256"] != file_state(SELECTION_BOARD)["sha256"]:
        pytest.skip("live_kicad: the open selection fixture differs from the repo copy")
    yield ref

    def clear(s):
        s.call(lambda b: b.set_selection(ref, []))
        return {}

    live_session.run_tool(clear)


def _select(session, ref, names: list[str]) -> None:
    """Select footprints (refdes) and pads (REF.PAD) by name, through the backend."""

    def body(s):
        index = s.call(lambda b: b.board_index(ref))
        ids = []
        for fp in index["footprints"]:
            if fp["ref"] in names:
                ids.append(fp["id"])
            for p in fp["pads"]:
                if f"{p.get('ref')}.{p.get('pad')}" in names:
                    ids.append(p["id"])
        assert len(ids) == len(names), (names, ids)
        s.call(lambda b: b.set_selection(ref, ids))
        return {}

    r = session.run_tool(body)
    assert r["ok"], r


def _selection(session, geometry=False):
    r = session.run_tool(lambda s: get_selection(s, include_geometry=geometry))
    assert r["ok"], r
    return r


def _kct(ref_path: str, *argv: str) -> dict:
    out = subprocess.run(
        [find_kct(), *argv[:1], ref_path, *argv[1:]],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    ).stdout
    return json.loads(out)


def _sets(sel):
    return set(sel["footprints"]), set(sel["pads"]), set(sel["nets"])


def _diff(before, after):
    return {
        k: {"lost": sorted(b - a), "gained": sorted(a - b)}
        for k, b, a in zip(("footprints", "pads", "nets"), before, after, strict=True)
        if a != b
    }


def test_footprints_round_trip_through_kct_check(live_session, board):
    _select(live_session, board, ["R1", "R2"])
    before = _selection(live_session, geometry=True)
    assert before["footprints"] == ["R1", "R2"]
    assert before["frame_offset"]["present"] is True

    report = _kct(board.path, "check", "--format", "json")
    wanted = set(before["footprints"])
    report["violations"] = [v for v in report["violations"] if set(v["items"]) <= wanted]
    assert report["violations"]

    r = live_session.run_tool(lambda s: show_findings(s, report, mode="both", zoom=True))
    assert r["ok"], r
    assert r["staleness"]["possibly_stale"] is False
    assert {u["part"] for u in r["unsupported"]} == {"highlight", "zoom"}
    after = _selection(live_session)
    assert not _diff(_sets(before), _sets(after)), _diff(_sets(before), _sets(after))
    for v in report["violations"]:
        [ref] = v["items"]
        pos = before["geometry"][ref]["position_mm"]
        assert abs(pos["x_mm"] - v["location"][0]) <= 1e-3
        assert abs(pos["y_mm"] - v["location"][1]) <= 1e-3


def test_pads_and_net_round_trip_through_kct_net_status(live_session, board):
    _select(live_session, board, ["R1.1", "R2.1"])
    before = _selection(live_session, geometry=True)
    assert before["pads"] == ["R1.1", "R2.1"]
    assert before["nets"] == ["VIN"]

    report = _kct(board.path, "net-status", "--format", "json", "--net", "VIN")
    r = live_session.run_tool(lambda s: show_findings(s, report))
    assert r["ok"], r
    after = _selection(live_session)
    assert not _diff(_sets(before), _sets(after)), _diff(_sets(before), _sets(after))
    points = r["findings"][0]["points"]
    for name in before["pads"]:
        pos = before["geometry"][name]["position_mm"]
        assert any(
            abs(p["file_mm"]["x_mm"] - pos["x_mm"]) <= 1e-3
            and abs(p["file_mm"]["y_mm"] - pos["y_mm"]) <= 1e-3
            for p in points
        ), f"no net-status point within 1 um of {name}"
