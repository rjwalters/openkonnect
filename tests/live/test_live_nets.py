"""LAY-04 live: ``list_nets`` against ``tests/fixtures/selection.kicad_pcb`` open in KiCad.

Never runs in CI. Setup as for ``test_live_selection.py``: KiCad 10 with 'Enable
KiCad API' on, the fixture open and unmodified. Run ``uv run pytest --live
-m live_kicad -rs``. Read-only. The net class names depend on the fixture's
project, so only the shape of the class field is checked.
"""

from __future__ import annotations

import os

import pytest

from openkonnect.tools.list_nets import list_nets, version_tuple

from ..conftest import SELECTION_BOARD

pytestmark = pytest.mark.live_kicad


@pytest.fixture
def opened(live_session):
    r = live_session.run_tool(lambda s: {"boards": s.call(lambda b: b.open_boards())})
    names = [os.path.basename(b.path) for b in r.get("boards") or [] if b.path]
    if os.path.basename(SELECTION_BOARD) not in names:
        pytest.skip(f"live_kicad: open {SELECTION_BOARD} in KiCad (open boards: {names})")
    return live_session


def test_live_nets_match_the_fixture(opened):
    r = opened.run_tool(lambda s: list_nets(s))
    assert r["ok"], r
    assert {n["name"]: n["pad_count"] for n in r["nets"]} == {"GND": 2, "SIG": 2, "VIN": 2}
    for n in r["nets"]:
        assert isinstance(n["net_class"], str) and n["net_class"]


def test_live_filters_and_items(opened):
    r = opened.run_tool(lambda s: list_nets(s, pattern="V*"))
    assert [n["name"] for n in r["nets"]] == ["VIN"]
    version = opened.run_tool(lambda s: {"v": s.call(lambda b: b.kicad_version())})["v"]
    r = opened.run_tool(lambda s: list_nets(s, include_items=True))
    if version_tuple(version) < (10, 0, 1):
        assert r["error"]["code"] == "unsupported_on_this_kicad"
        return
    assert r["ok"], r
    vin = {n["name"]: n for n in r["nets"]}["VIN"]
    assert vin["pads"] == ["R1.1", "R2.1"]
    assert len(vin["other"]) == 3
